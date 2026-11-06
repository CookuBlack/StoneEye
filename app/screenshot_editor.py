# -*- coding: utf-8 -*-
"""截图编辑态：框选完成后，先标注再决定去向（微信 / QQ 截图那套）。

流程：框选 -> 抓整屏冻结 -> 本窗口盖住全屏并挖出选区 -> 工具条标注
      -> 完成(复制+贴图) / 钉桌面(只贴图) / 保存 / 发送 / 取消。

和原有功能的关系：点「完成」「钉在桌面」仍会把结果交给 WindowManager
生成原来那张置顶贴图并进 OCR 队列，所以「持续显示截屏区域 + 自动标景别/运镜」
的核心行为没有丢，只是中间多了一层可标注的编辑态。

坐标：窗口内一律用 Qt 逻辑像素（主程序关了 high-DPI，正常 == 物理像素），
合成输出用物理像素；两者靠 ui_scale() 换算，setWindow/setViewport 一步对齐。
"""
import ctypes
import math
import os
import sys
import time

from PySide6.QtCore import (
    QEvent, QEventLoop, QPoint, QPointF, QRect, QRectF, Qt, QThread, QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPen,
    QPixmap, QBrush,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from .capture_toolbar import CaptureToolbar, OptionsBar, StickerPicker
from .capture_icons import CLICK_TOOLS, DRAW_TOOLS
from .coords import to_logical, to_physical, ui_scale, ui_zoom
from .screen_capture import Rect, capture
from .ui_shell import ShellWindow

TIP = "拖动绘制   ·   Ctrl+Z 撤销   ·   Ctrl+S 保存   ·   Enter 完成   ·   Esc 取消"
SETTLE_MS = 110

# --- Windows Z 序 ---------------------------------------------------------
# 编辑器是全屏 topmost 窗口，工具条 / 选项条 / 表情板 / OCR 面板是另外几个
# topmost 顶层窗口。只靠 raise_() 不够：用户点过附属窗口再点回画布时，
# Windows 会把编辑器提到 topmost 组最顶，把整条工具条盖住（V4.8 实测的
#「点一下工具条就消失」）。把附属窗口的 owner 设成编辑器之后，Windows
# 从窗口管理器层面保证 owned window 永远在 owner 之上，激活 owner 也不会
# 反过来压住它。tools/check_zorder.py 用 WindowFromPoint 做回归验证。
if sys.platform == "win32":
    from ctypes import wintypes
    _USER32 = ctypes.windll.user32
    _SET_LONG = (getattr(_USER32, "SetWindowLongPtrW", None)
                 or _USER32.SetWindowLongW)
    _SET_LONG.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _SET_LONG.restype = ctypes.c_void_p
    _GWLP_HWNDPARENT = -8
else:
    _USER32 = None


def _pil_to_pixmap(img) -> QPixmap:
    rgb = img.convert("RGB")
    qi = QImage(rgb.tobytes(), rgb.width, rgb.height,
                rgb.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qi.copy())


def pixmap_to_pil(pm: QPixmap):
    """QPixmap -> PIL.Image（给 make_frame 用）。"""
    from PIL import Image
    qi = pm.toImage().convertToFormat(QImage.Format_RGB888)
    w, h = qi.width(), qi.height()
    bpl = qi.bytesPerLine()
    buf = bytes(qi.constBits())
    data = b"".join(buf[y * bpl:y * bpl + w * 3] for y in range(h))
    return Image.frombytes("RGB", (w, h), data)


def _virtual_geometry() -> QRect:
    screens = list(QGuiApplication.screens())
    if not screens:
        return QRect(0, 0, 1920, 1080)
    left = min(s.geometry().left() for s in screens)
    top = min(s.geometry().top() for s in screens)
    right = max(s.geometry().right() for s in screens)
    bottom = max(s.geometry().bottom() for s in screens)
    return QRect(left, top, right - left + 1, bottom - top + 1)


def capture_desktop():
    """抓整个虚拟桌面，返回 (QPixmap, 物理 Rect)。调用前要藏好所有置顶窗口。"""
    geom = _virtual_geometry()
    rect = Rect(*to_physical(geom.x(), geom.y(),
                             geom.width(), geom.height()))
    return _pil_to_pixmap(capture(rect)), rect


def _pos(event):
    try:
        return event.position().toPoint()
    except AttributeError:
        return event.pos()


class _OcrTextWorker(QThread):
    """单独开一个引擎跑 OCR，避免把编辑器的 UI 线程卡住。"""
    finished = Signal(str)

    def __init__(self, image, cfg, parent=None):
        super().__init__(parent)
        self._image = image
        self._cfg = cfg

    def run(self):
        try:
            from .ocr_engine import OcrEngine
            items = OcrEngine(self._cfg).ocr(self._image)
            rows = []
            for it in items:
                text = str(it.get("text", "")).strip()
                if not text:
                    continue
                box = it.get("box") or []
                y = min((float(p[1]) for p in box), default=0.0)
                x = min((float(p[0]) for p in box), default=0.0)
                rows.append((y, x, text))
            rows.sort(key=lambda r: (round(r[0] / 12), r[1]))
            self.finished.emit("\n".join(r[2] for r in rows) or "（没认出文字）")
        except Exception as exc:
            self.finished.emit("识别失败：%s" % exc)


class OcrTextPanel(ShellWindow):
    """OCR 提取结果：只读文本 + 复制 / 关闭。"""

    RADIUS = 12
    PAD = 14

    def __init__(self, text, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("提取文字")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self._spad, self._spad, self._spad, self._spad)
        lay.setSpacing(round(8 * self._scale))
        title = QLabel("提取文字")
        title.setStyleSheet("color:#EFEFEF;font-weight:600;font-size:15px;")
        lay.addWidget(title)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        box.setMinimumSize(round(360 * self._scale), round(190 * self._scale))
        box.setStyleSheet(
            "QTextEdit{background:#181818;color:#DEDEDE;border:1px solid #333;"
            "border-radius:8px;padding:8px;selection-background-color:#3A5A80;}")
        lay.addWidget(box)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_copy = QPushButton("复制全部")
        btn_close = QPushButton("关闭")
        for b in (btn_copy, btn_close):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#2A2A2A;color:#E6E6E6;border:1px solid #444;"
                "border-radius:7px;padding:6px 16px;}"
                "QPushButton:hover{background:#363636;}")
            row.addWidget(b)
        lay.addLayout(row)
        btn_copy.clicked.connect(lambda: (
            QApplication.clipboard().setText(text),
            btn_copy.setText("已复制")))
        btn_close.clicked.connect(self.hide)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.ensure_rim()


class ScreenshotEditor(QWidget):
    """全屏编辑窗口；exec() 阻塞到用户点完工具条，返回结果字典。"""

    def __init__(self, screen_pm: QPixmap, full_rect: Rect, sel_rect: Rect,
                 cfg=None, parent=None):
        super().__init__(parent)
        self._cfg = cfg or {}
        self._geom = _virtual_geometry()
        self._full = full_rect
        self._sel_phys = sel_rect
        self._screen_pm = screen_pm
        self._scale = ui_scale()
        self._zoom = ui_zoom()

        gx, gy = to_logical(sel_rect.x, sel_rect.y)
        self._sel = QRect(
            gx - self._geom.left(), gy - self._geom.top(),
            max(1, round(sel_rect.w / self._scale)),
            max(1, round(sel_rect.h / self._scale)))
        self._crop = QRect(
            round(sel_rect.x - full_rect.x), round(sel_rect.y - full_rect.y),
            sel_rect.w, sel_rect.h)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setGeometry(self._geom)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        self._items = []          # 撤销栈
        self._draft = None
        self._number = 1
        self._sticker = "😀"
        self._tool = "rect"
        self._color = QColor("#F2645B")
        self._width = 3
        self._font_size = 20
        self._long_pm = None
        self._result = None
        self._loop = None
        self._text_edit = None
        self._ocr_worker = None
        self._ocr_panel = None
        self._toast_label = None
        self._toast_timer = None
        self._busy = False
        self._raise_pending = False

        self._mosaic_pm = QPixmap(screen_pm.size())
        self._mosaic_pm.fill(Qt.transparent)

        self._toolbar = CaptureToolbar()
        self._options = OptionsBar()
        self._picker = StickerPicker()
        self._toolbar.toolSelected.connect(self._set_tool)
        self._toolbar.actionTriggered.connect(self._on_action)
        self._options.colorChanged.connect(self._set_color)
        self._options.widthChanged.connect(self._set_width)
        self._options.sizeChanged.connect(self._set_font_size)
        self._picker.picked.connect(self._on_sticker)

    # ---------- 对外 ----------
    def show_editor(self):
        """把编辑态摆上屏幕（不进局部事件循环，自检脚本也走这条真实路径）。"""
        self.show()
        self.raise_()
        self.activateWindow()
        self._attach_all()
        self._place_children()
        self._show_child(self._toolbar)
        self._set_tool("rect")

    def exec(self):
        self.show_editor()
        self._loop = QEventLoop()
        if self._result is None:
            self._loop.exec()
        self._hide_children()
        self.hide()
        self._settle(SETTLE_MS)
        return self._result or {"action": "cancel", "pixmap": None}

    def result(self):
        return self._result

    def cleanup(self):
        """一次截图结束后销毁所有附属窗口。

        工具条 / 选项条 / 表情板 / OCR 面板都是**独立顶层窗口**（不是
        editor 的子控件），editor.deleteLater() 带不走它们。每次框选都新建
        一套，不显式释放就会一次漏 4 个 HWND。
        OCR 线程也必须等它收工：带着运行中的 QThread 析构会直接崩。
        """
        if self._ocr_worker is not None:
            try:
                self._ocr_worker.finished.disconnect()
            except Exception:
                pass
            if self._ocr_worker.isRunning():
                self._ocr_worker.wait(6000)
            self._ocr_worker = None
        self._hide_children()
        for w in (self._toolbar, self._options, self._picker, self._ocr_panel):
            if w is not None:
                w.hide()
                w.deleteLater()
        self._toolbar = self._options = self._picker = self._ocr_panel = None
        self.deleteLater()

    # ---------- 摆位 ----------
    def _sel_global(self) -> QRect:
        return self._sel.translated(self._geom.topLeft())

    def _place_children(self):
        bar = self._toolbar.place_for(self._sel_global(), self._geom)
        self._options.place_above(bar, self._geom)
        self._picker.place_above(bar, self._geom)

    def _hide_children(self):
        for w in (self._toolbar, self._options, self._picker, self._ocr_panel):
            if w is not None:
                w.hide()
        if self._text_edit is not None:
            self._text_edit.hide()

    # ---------- 层级 ----------
    def _attach(self, widget):
        """把浮窗登记为本编辑器的 owned window（仅 Windows）。

        winId() 会强制创建原生 HWND，所以隐藏着的窗口也能提前挂好；
        Qt 在某些 show / raise 路径上会重写 owner，因此每次显示时都重挂一遍。
        """
        if widget is None or _USER32 is None:
            return
        try:
            owner = int(self.winId())
            child = int(widget.winId())
            if owner and child and owner != child:
                _SET_LONG(child, _GWLP_HWNDPARENT, ctypes.c_void_p(owner))
        except Exception:
            pass

    def _attach_all(self):
        for w in (self._toolbar, self._options, self._picker, self._ocr_panel):
            self._attach(w)

    def _pin(self, widget):
        """挂 owner + 置顶，再复查一次 owner。

        顺序很关键：Qt 在窗口首次 show() / 某些 raise 路径上会重建原生
        HWND 或把 owner 清成 0（实测 show 之后 GetWindow(GW_OWNER) 归零），
        所以显示前挂的那次不算数，必须在显示之后再挂一遍。
        """
        if widget is None:
            return
        self._attach(widget)
        widget.raise_()
        self._attach(widget)

    def _show_child(self, widget):
        if widget is None:
            return
        widget.show()
        self._pin(widget)

    def _raise_children(self):
        """兜底：前台切换 / 系统弹窗 / 点击画布之后，把可见浮窗捞回最上层。"""
        for w in (self._toolbar, self._options, self._picker, self._ocr_panel):
            if w is not None and w.isVisible():
                self._pin(w)

    def _resume(self):
        """从文件对话框 / 截长图 / 系统弹窗回来后恢复整组浮窗。

        这类窗口往往是 parent=None 的模态框，关闭后前台不一定回到编辑器，
        所以必须重新激活并走一遍 _show_child（它会重建 owner 顺序）。
        """
        self.show()
        self.activateWindow()
        self._place_children()
        self._show_child(self._toolbar)
        if self._tool in DRAW_TOOLS:
            self._show_child(self._options)

    def event(self, ev):
        # 只监听激活，不监听 ZOrderChange：raise_() 自己会触发后者，
        # 用它做兜底会变成「raise -> 事件 -> 再 raise」的死循环。
        if (ev.type() == QEvent.WindowActivate and self.isVisible()
                and not self._raise_pending):
            self._raise_pending = True
            # 延后一拍，等 Windows 把本次前台切换的 Z 序调整完
            QTimer.singleShot(0, self._raise_now)
        return super().event(ev)

    def _raise_now(self):
        self._raise_pending = False
        if self.isVisible():
            self._raise_children()

    @staticmethod
    def _settle(ms):
        deadline = time.monotonic() + ms / 1000.0
        while True:
            QApplication.processEvents()
            if time.monotonic() >= deadline:
                return
            time.sleep(0.006)

    # ---------- 工具切换 ----------
    def _set_tool(self, tool):
        self._tool = tool
        self._toolbar.set_tool(tool)
        drawing = tool in DRAW_TOOLS
        if drawing and self.isVisible():
            self._options.set_mode(tool)
            self._place_children()
            self._show_child(self._options)
        else:
            self._options.hide()
        if tool == "sticker":
            self._show_child(self._picker)
        else:
            self._picker.hide()
        self.setCursor(Qt.CrossCursor if tool not in ("ocr", "long")
                       else Qt.ArrowCursor)
        self.update()

    def _set_color(self, value):
        self._color = QColor(value)

    def _set_width(self, value):
        self._width = int(value)

    def _set_font_size(self, value):
        self._font_size = int(value)

    def _on_sticker(self, ch):
        self._sticker = ch
        self._picker.hide()
        self._toast("已选 %s，在选区里点一下放置" % ch)

    # ---------- 工具栏动作 ----------
    def _on_action(self, action):
        if self._busy:
            return
        if action == "undo":
            self._undo()
        elif action == "cancel":
            self._finish("cancel")
        elif action == "save":
            self._save()
        elif action == "ocr":
            self._run_ocr()
        elif action == "long":
            self._run_long()
        elif action in ("done", "share", "pin"):
            pm = self._compose()
            if action in ("done", "share"):
                QApplication.clipboard().setPixmap(pm)
                self._toast("已复制到剪贴板" if action == "share"
                            else "已复制，正在生成贴图")
            self._finish(action, pm)

    def _undo(self):
        if not self._items:
            self._toast("没有可撤销的标注")
            return
        item = self._items.pop()
        if item["kind"] == "mosaic":
            self._rebuild_mosaic()
        self._toolbar.set_undo_enabled(bool(self._items))
        self.update()

    def _save(self):
        pm = self._compose()
        self._hide_children()
        self.hide()
        self._settle(60)
        from PySide6.QtCore import QStandardPaths
        from datetime import datetime
        base = (QStandardPaths.writableLocation(
            QStandardPaths.PicturesLocation) or os.path.expanduser("~"))
        name = "StoneEye_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
        path, _ = QFileDialog.getSaveFileName(
            None, "保存截图", os.path.join(base, name), "PNG 图片 (*.png)")
        if not path:
            self._resume()
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        ok = pm.save(path, "PNG")
        self._finish("save" if ok else "cancel", pm, path)

    def _finish(self, action, pixmap=None, path=""):
        self._result = {
            "action": action, "pixmap": pixmap, "path": path,
            "rect": self._sel_phys,
        }
        self._hide_children()
        self.hide()
        if self._loop is not None and self._loop.isRunning():
            self._loop.quit()

    # ---------- OCR ----------
    def _run_ocr(self):
        from PIL import Image
        crop = self._compose_base()
        img = pixmap_to_pil(crop)
        self._busy = True
        self._toolbar.set_busy(True)
        self._toast("正在识别文字…")
        self._ocr_worker = _OcrTextWorker(img, self._cfg)
        self._ocr_worker.finished.connect(self._on_ocr_done)
        self._ocr_worker.start()

    def _on_ocr_done(self, text):
        self._busy = False
        self._toolbar.set_busy(False)
        if self._ocr_panel is None:
            self._ocr_panel = OcrTextPanel(text)
        else:
            self._ocr_panel.deleteLater()
            self._ocr_panel = OcrTextPanel(text)
        g = self._sel_global()
        x = max(self._geom.left() + 8,
                min(g.center().x() - self._ocr_panel.width() // 2,
                    self._geom.right() - self._ocr_panel.width() - 8))
        y = max(self._geom.top() + 8, g.top() + 16)
        self._ocr_panel.move(x, y)
        self._show_child(self._ocr_panel)

    # ---------- 截长图 ----------
    def _run_long(self):
        from .scroll_capture import capture_long
        self._busy = True
        self._toolbar.set_busy(True)
        self._hide_children()
        self.hide()
        self._settle(150)
        try:
            img, used = capture_long(self._sel_phys, steps=8, delay=0.30)
        except Exception as exc:
            QMessageBox.warning(None, "截长图失败", str(exc))
            img, used = None, 0
        self._busy = False
        self._toolbar.set_busy(False)
        self._resume()
        if img is None:
            self._toast("截长图没成功，可以再试一次")
            return
        self._long_pm = _pil_to_pixmap(img)
        self._items.clear()
        self._rebuild_mosaic()
        self._toolbar.set_undo_enabled(False)
        self._options.hide()
        self._toast("长图 %d×%d（%d 帧）· 可直接完成 / 保存"
                    % (img.width, img.height, used))
        self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if not self._screen_pm.isNull():
            p.drawPixmap(QRect(self.rect()), self._screen_pm,
                         QRect(self._screen_pm.rect()))

        # 选区外压暗：上下左右四块，不用 CompositionMode_Clear，透明窗口下更稳
        s = self._sel
        dark = QColor(0, 0, 0, 118)
        for r in (QRect(0, 0, self.width(), s.top()),
                  QRect(0, s.bottom(), self.width(),
                        self.height() - s.bottom()),
                  QRect(0, s.top(), s.left(), s.height()),
                  QRect(s.right(), s.top(), self.width() - s.right(),
                        s.height())):
            p.fillRect(r, dark)

        if self._long_pm is not None:
            p.save()
            p.setClipRect(s)
            p.fillRect(s, QColor(18, 18, 18))
            scaled = self._long_pm.scaled(
                s.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(s.center().x() - scaled.width() // 2,
                         s.center().y() - scaled.height() // 2, scaled)
            p.restore()
        else:
            if not self._mosaic_pm.isNull():
                p.save()
                p.setClipRect(s)
                p.drawPixmap(QRect(self.rect()), self._mosaic_pm,
                             QRect(self._mosaic_pm.rect()))
                p.restore()
            self._draw_annotations(p)

        p.setPen(QPen(QColor(0, 229, 255), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(s).adjusted(0.5, 0.5, -0.5, -0.5))
        self._paint_tip(p)

    def _paint_tip(self, p):
        label = ("长图已生成 · 点 ✔ 完成 / ⬇ 保存" if self._long_pm is not None
                 else TIP)
        f = QFont()
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        w = fm.horizontalAdvance(label) + 24
        h = fm.height() + 10
        x = max(4, min(self._sel.left(), self.width() - w - 4))
        y = self._sel.top() - h - 6
        if y < 4:
            y = min(self._sel.bottom() + 6, self.height() - h - 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 20, 26, 215))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        p.setPen(QColor(235, 245, 255))
        p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, label)

    def _draw_annotations(self, p: QPainter):
        p.save()
        p.setClipRect(self._sel)
        for item in self._items:
            self._draw_one(p, item)
        if self._draft is not None:
            self._draw_one(p, self._draft)
        p.restore()

    def _draw_one(self, p: QPainter, a):
        kind = a["kind"]
        col = QColor(a.get("color", "#F2645B"))
        width = float(a.get("width", 3))
        pen = QPen(col, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        if kind in ("rect", "ellipse"):
            r = QRectF(QPointF(a["p1"]), QPointF(a["p2"])).normalized()
            if kind == "rect":
                p.drawRect(r)
            else:
                p.drawEllipse(r)
        elif kind == "arrow":
            p1, p2 = QPointF(a["p1"]), QPointF(a["p2"])
            p.drawLine(p1, p2)
            ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
            head = max(9.0, width * 4.0)
            for delta in (0.45, -0.45):
                p.drawLine(p2, p2 - QPointF(
                    head * math.cos(ang + delta),
                    head * math.sin(ang + delta)))
        elif kind == "pen":
            pts = a.get("points") or []
            if len(pts) >= 2:
                p.drawPolyline([QPointF(q) for q in pts])
        elif kind == "text":
            f = QFont("Microsoft YaHei UI")
            f.setPixelSize(int(a.get("size", 20) * self._zoom))
            f.setBold(True)
            p.setFont(f)
            p.drawText(QPointF(a["pos"]) + QPointF(0, 0), a["text"])
        elif kind == "number":
            c = QPointF(a["pos"])
            r = 14 * self._zoom
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(c, r, r)
            f = QFont("Segoe UI")
            f.setPixelSize(int(r * 1.15))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(c.x() - r, c.y() - r, r * 2, r * 2),
                       Qt.AlignCenter, str(a["n"]))
        elif kind == "sticker":
            f = QFont("Segoe UI Emoji")
            f.setPixelSize(int(30 * self._zoom))
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(QPointF(a["pos"]) - QPointF(20, 20),
                              QPointF(a["pos"]) + QPointF(20, 20)),
                       Qt.AlignCenter, a["char"])

    # ---------- 马赛克 ----------
    def _to_base_phys(self, pt: QPoint) -> QPoint:
        s = self._scale
        gx = (pt.x() + self._geom.left()) * s
        gy = (pt.y() + self._geom.top()) * s
        return QPoint(round(gx - self._full.x), round(gy - self._full.y))

    def _mosaic_square(self, center: QPoint):
        size = round(18 * self._zoom)
        q = QPoint(center.x() - size // 2, center.y() - size // 2)
        p = QPainter(self._mosaic_pm)
        p.setClipRect(self._crop)
        src = self._screen_pm.copy(QRect(q, QPoint(q.x() + size, q.y() + size)))
        tiny = src.scaled(7, 7, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        block = tiny.scaled(size, size, Qt.IgnoreAspectRatio,
                            Qt.FastTransformation)
        p.drawPixmap(q, block)
        p.end()
        return (q.x(), q.y(), size)

    def _rebuild_mosaic(self):
        self._mosaic_pm.fill(Qt.transparent)
        for item in self._items:
            if item["kind"] != "mosaic":
                continue
            for x, y, size in item["squares"]:
                q = QPoint(x, y)
                p = QPainter(self._mosaic_pm)
                p.setClipRect(self._crop)
                src = self._screen_pm.copy(QRect(x, y, size, size))
                tiny = src.scaled(7, 7, Qt.IgnoreAspectRatio,
                                  Qt.FastTransformation)
                p.drawPixmap(q, tiny.scaled(size, size, Qt.IgnoreAspectRatio,
                                            Qt.FastTransformation))
                p.end()

    # ---------- 鼠标 ----------
    def mousePressEvent(self, event):
        # 点画布会把编辑器激活到前台，顺手把浮窗捞回最上层
        self._raise_children()
        if event.button() == Qt.RightButton:
            self._finish("cancel")
            return
        if event.button() != Qt.LeftButton or self._busy:
            return
        pt = _pos(event)
        if not self._sel.contains(pt):
            return
        if self._long_pm is not None:
            self._toast("长图已生成，点 ✔ 完成 或 ⬇ 保存")
            return

        tool = self._tool
        base = dict(color=self._color.name(), width=self._width)
        if tool == "text":
            self._begin_text(pt)
            return
        if tool == "number":
            self._items.append(dict(base, kind="number", pos=pt,
                                    n=self._number))
            self._number += 1
        elif tool == "sticker":
            self._items.append(dict(base, kind="sticker", pos=pt,
                                    char=self._sticker))
        elif tool in ("rect", "ellipse", "arrow"):
            self._draft = dict(base, kind=tool, p1=pt, p2=pt)
        elif tool == "pen":
            self._draft = dict(base, kind="pen", points=[pt])
        elif tool == "mosaic":
            sq = self._mosaic_square(self._to_base_phys(pt))
            self._draft = dict(base, kind="mosaic", squares=[sq])
        else:
            return
        self._toolbar.set_undo_enabled(True)
        self.update()

    def mouseMoveEvent(self, event):
        if self._draft is None:
            return
        pt = _pos(event)
        kind = self._draft["kind"]
        if kind in ("rect", "ellipse", "arrow"):
            self._draft["p2"] = pt
        elif kind == "pen":
            self._draft["points"].append(pt)
        elif kind == "mosaic":
            self._draft["squares"].append(
                self._mosaic_square(self._to_base_phys(pt)))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._draft is None:
            return
        d = self._draft
        self._draft = None
        if d["kind"] == "pen" and len(d["points"]) < 2:
            return
        if d["kind"] == "mosaic" and not d["squares"]:
            return
        self._items.append(d)
        self._toolbar.set_undo_enabled(True)
        self.update()

    def _begin_text(self, pt):
        if self._text_edit is not None:
            self._text_edit.deleteLater()
        ed = QLineEdit(self)
        ed.setPlaceholderText("输入文字，回车确认")
        ed.setFixedWidth(max(180, round(220 * self._zoom)))
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(int(self._font_size * self._zoom))
        ed.setFont(f)
        ed.setStyleSheet(
            "QLineEdit{background:#1B1B1B;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:5px 8px;}"
            % (self._color.name(), self._color.name()))
        ed.move(pt.x(), pt.y() - ed.height() // 2)
        ed.installEventFilter(self)
        self._text_edit = ed
        ed.returnPressed.connect(lambda: self._commit_text(pt))
        ed.show()
        ed.setFocus()

    def _commit_text(self, pt):
        ed = self._text_edit
        if ed is None:
            return
        text = ed.text().strip()
        self._text_edit = None
        ed.hide()
        ed.deleteLater()
        if text:
            self._items.append(dict(
                kind="text", pos=QPoint(pt.x(), pt.y() +
                                        int(self._font_size * self._zoom * 0.35)),
                text=text, color=self._color.name(), size=self._font_size))
            self._toolbar.set_undo_enabled(True)
        self.setFocus()
        self.update()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self._text_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                ed = self._text_edit
                self._text_edit = None
                ed.hide()
                ed.deleteLater()
                self.setFocus()
                return True
        return super().eventFilter(obj, event)

    # ---------- 键盘 ----------
    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Escape:
            self._finish("cancel")
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._on_action("done")
        elif key == Qt.Key_Z and mods & Qt.ControlModifier:
            self._undo()
        elif key == Qt.Key_S and mods & Qt.ControlModifier:
            self._save()
        else:
            super().keyPressEvent(event)

    # ---------- 提示 ----------
    def _toast(self, text):
        if self._toast_label is None:
            self._toast_label = QLabel(self)
            self._toast_label.setStyleSheet(
                "QLabel{background:#1B1B1B;color:#EDEDED;border:1px solid #444;"
                "border-radius:8px;padding:7px 14px;font-size:13px;}")
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(
                lambda: self._toast_label.hide()
                if self._toast_label is not None else None)
        self._toast_label.setText(text)
        self._toast_label.adjustSize()
        x = max(4, min(self._sel.left() + 8,
                       self.width() - self._toast_label.width() - 4))
        y = max(4, min(self._sel.bottom() - self._toast_label.height() - 8,
                       self.height() - self._toast_label.height() - 4))
        self._toast_label.move(x, y)
        self._toast_label.show()
        self._toast_label.raise_()
        self._toast_timer.start(1900)

    # ---------- 合成 ----------
    def _compose_base(self) -> QPixmap:
        if self._long_pm is not None:
            return self._long_pm
        out = QPixmap(self._sel_phys.w, self._sel_phys.h)
        out.fill(QColor(0, 0, 0, 0))
        p = QPainter(out)
        p.drawPixmap(QRect(0, 0, out.width(), out.height()),
                     self._screen_pm, QRect(self._crop))
        p.drawPixmap(QRect(0, 0, out.width(), out.height()),
                     self._mosaic_pm, QRect(self._crop))
        p.end()
        return out

    def _compose(self) -> QPixmap:
        out = self._compose_base()
        if self._long_pm is not None or not self._items:
            return out
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setViewport(QRect(0, 0, out.width(), out.height()))
        p.setWindow(self._sel)
        self._draw_annotations(p)
        p.end()
        return out
