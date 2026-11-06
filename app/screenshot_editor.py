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
    QColor, QFont, QFontMetrics, QGuiApplication, QImage, QLinearGradient,
    QPainter, QPen, QPixmap, QBrush,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from .brand import (
    EDGE_BAND, GRIP_HIT, MIN_SELECTION, brand_hex, brand_qcolor,
    clamp_selection, draw_selection_frame, hit_selection, resize_selection,
)
from .capture_toolbar import (
    CaptureToolbar, OptionsBar, StickerPicker, ToolSurface,
)
from .capture_icons import CLICK_TOOLS, DRAW_TOOLS
from .coords import to_logical, to_physical, ui_scale, ui_zoom
from .screen_capture import Rect, capture

TIP_MAIN = ("拖边框移动   ·   拖白点改大小   ·   框外可重框   ·   "
            "Alt+拖动整框移动")
TIP_KEYS = ("拖动绘制   ·   Ctrl+Z 撤销   ·   Ctrl+S 保存   ·   "
            "Enter 完成   ·   Esc 取消")
TIP = TIP_MAIN + "   ·   " + TIP_KEYS       # 兼容旧引用
SETTLE_MS = 110

_RESIZE_CURSORS = {
    "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
    "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
    "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
    "w": Qt.SizeHorCursor, "e": Qt.SizeHorCursor,
}

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
    """单独开一个引擎跑 OCR，避免把编辑器的 UI 线程卡住。

    结果信号不能叫 `finished`：QThread 自己有一个无参的 finished()，
    Python 子类用同名 Signal(str) 覆盖会抢掉 C++ 的线程结束通知，
    PySide 分发时还可能报参数不匹配。改名 resultReady，把
    QThread.finished 留给「线程收工后兜底解锁工具条」。
    """
    resultReady = Signal(str)

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
            self.resultReady.emit("\n".join(r[2] for r in rows) or "（没认出文字）")
        except Exception as exc:
            self.resultReady.emit("识别失败：%s" % exc)


class OcrTextPanel(ToolSurface):
    """OCR 提取结果：只读文本 + 复制 / 关闭。

    同一个面板可以换文字复用 —— 连点两次「提取文字」不该叠出两个窗口。
    """

    RADIUS = 13

    def __init__(self, text, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("提取文字")
        self._text = text
        lay = QVBoxLayout(self)
        m = self.content_margins()
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(round(9 * self._scale))

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(2, 0, 0, 0)
        hl.setSpacing(round(8 * self._scale))
        dot = QLabel()
        d = self._px(8)
        dot.setFixedSize(d, d)
        dot.setStyleSheet("background:%s;border-radius:%dpx;"
                          % (brand_hex(), d // 2))
        title = QLabel("提取文字")
        title.setStyleSheet(
            "color:#F2F5FA;font-weight:700;font-size:15px;"
            "background:transparent;")
        hl.addWidget(dot)
        hl.addWidget(title)
        hl.addStretch(1)
        lay.addWidget(head)

        self._box = QTextEdit()
        self._box.setReadOnly(True)
        self._box.setPlainText(text)
        self._box.setMinimumSize(round(360 * self._scale),
                                 round(190 * self._scale))
        self._box.setStyleSheet(
            "QTextEdit{background:#151920;color:#DEE5EF;"
            "border:1px solid rgba(255,255,255,26);border-radius:9px;"
            "padding:9px;selection-background-color:%s;}" % brand_hex())
        lay.addWidget(self._box)

        row = QHBoxLayout()
        row.addStretch(1)
        self._btn_copy = QPushButton("复制全部")
        btn_close = QPushButton("关闭")
        self._btn_copy.setStyleSheet(
            "QPushButton{background:%s;color:#101820;border:0;"
            "border-radius:8px;padding:7px 18px;font-weight:700;}"
            "QPushButton:hover{background:#EAF6FF;}" % brand_hex())
        btn_close.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,18);color:#DCE3EE;"
            "border:1px solid rgba(255,255,255,30);border-radius:8px;"
            "padding:7px 18px;}"
            "QPushButton:hover{background:rgba(255,255,255,46);color:#FFFFFF;}")
        for b in (self._btn_copy, btn_close):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            row.addWidget(b)
        lay.addLayout(row)
        self._btn_copy.clicked.connect(self._copy_all)
        btn_close.clicked.connect(self.hide)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.ensure_rim()

    def _copy_all(self):
        QApplication.clipboard().setText(self._text)
        self._btn_copy.setText("已复制 ✓")

    def set_text(self, text):
        self._text = text
        self._box.setPlainText(text)
        self._btn_copy.setText("复制全部")


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
        self._width = 4
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
        self._last_child = None
        # 手动滚轮截长图会话（V4.9.2）：编辑器隐藏期间靠 HUD + QTimer 驱动
        self._long_session = None
        self._long_hud = None
        self._long_timer = None
        self._long_active = False
        self._long_overlap = False
        # 可选注入点：离屏自检用合成帧喂会话，不必真的抓屏
        self._long_factory = None
        # 选区调整会话（V4.9.3）：None 或
        # {"mode": resize/move/draw, "handle", "start", "origin", "rect"}
        self._adjust = None
        self._cursor_kind = "tool"

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
        # 选项条在构造时就已经定好默认色 / 线宽 / 字号，那时信号还没连上；
        # 主动同步一次，避免编辑器画的和选项条高亮的不是同一档。
        self._set_color(self._options.color)
        self._set_width(self._options.pen_width)
        self._set_font_size(self._options.font_size)

    # ---------- 对外 ----------
    @classmethod
    def for_image(cls, pm: QPixmap, cfg=None, parent=None):
        """用一张现成的图开二次编辑：不抓屏，深色编辑台居中，选区正好是这张图。

        「确定」之后手里只有贴图的成品图（原画面 + 上一次的手绘标注），
        没有整屏可以冻结。这里造一张虚拟桌面尺寸的画布把图居中放上去，
        其余全部复用原来的坐标、撤销栈和合成链路；输出会缩回原分辨率，
        回写贴图时尺寸不会变。
        """
        geom = _virtual_geometry()
        # _screen_pm / _crop 走物理像素，窗口 / _sel 走逻辑像素，两者差一个
        # ui_scale()。画布必须按物理尺寸建，否则缩放屏上框和底图会错开。
        s = ui_scale()
        W = max(1, round(geom.width() * s))
        H = max(1, round(geom.height() * s))
        canvas = QPixmap(W, H)
        painter = QPainter(canvas)
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, QColor(24, 28, 38))
        grad.setColorAt(1.0, QColor(11, 13, 18))
        painter.fillRect(canvas.rect(), QBrush(grad))
        painter.setPen(QPen(QColor(255, 255, 255, 7), 1.0))
        step = max(16, round(48 * s))
        for gx in range(0, W, step):
            painter.drawLine(gx, 0, gx, H)
        for gy in range(0, H, step):
            painter.drawLine(0, gy, W, gy)

        max_w = max(120, W - round(120 * s))
        max_h = max(120, H - round(220 * s))
        k = min(1.0, max_w / max(1, pm.width()),
                max_h / max(1, pm.height()))
        src = pm if k >= 0.999 else pm.scaled(
            max(1, round(pm.width() * k)), max(1, round(pm.height() * k)),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x, y = (W - src.width()) // 2, (H - src.height()) // 2
        painter.drawPixmap(x, y, src)
        painter.end()

        fx, fy = to_physical(geom.left(), geom.top(), 1, 1)[:2]
        full = Rect(fx, fy, W, H)
        sel = Rect(fx + x, fy + y, src.width(), src.height())
        ed = cls(canvas, full, sel, cfg, parent)
        ed._export_size = (pm.width(), pm.height())
        ed._is_reedit = True
        return ed

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
        # 长图模式是异步的：不先停掉 timer / HUD，编辑器析构后会留下一个
        # 永远在转的采样循环和一块无主的顶层浮窗
        self._end_long_mode(resume=False)
        if self._ocr_worker is not None:
            # 两个信号都要断：结果信号会弹面板，线程结束信号会摸工具条，
            # 而它们此刻都已经被隐藏 / 准备销毁了。
            for sig in (self._ocr_worker.resultReady,
                        self._ocr_worker.finished):
                try:
                    sig.disconnect()
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
        self._last_child = widget
        self._pin(widget)

    def _raise_children(self):
        """兜底：前台切换 / 系统弹窗 / 点击画布之后，把可见浮窗捞回最上层。

        顺序不能写死：OCR 面板和表情板可能同时在屏幕上，固定顺序会让
        早先弹出的 OCR 面板把用户刚切出来的表情板压住。最近显示过的
        那个最后 pin，Windows 就把它留在 owned window 组最上面。
        """
        order = [w for w in (self._toolbar, self._options,
                             self._picker, self._ocr_panel)
                 if w is not None and w.isVisible()]
        last = self._last_child
        if last in order:
            order.remove(last)
            order.append(last)
        for w in order:
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
        # 点击型工具给手型，拖拽型给十字，动作按钮不再进入这里
        self.setCursor(Qt.PointingHandCursor if tool in CLICK_TOOLS
                       else Qt.CrossCursor)
        self._cursor_kind = "tool:"
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
        # 识别 / 截长图期间其余动作一律拦下，但「取消」必须随时能按
        if self._busy and action != "cancel":
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
        self._renumber()
        self._toolbar.set_undo_enabled(bool(self._items))
        self.update()

    def _renumber(self):
        """重排序号：撤销后画面上永远是从 1 开始的连续 1、2、3…。

        老实现只在 mousePress 里 `_number += 1`，撤销时计数器不回退，
        标了 6 个再撤销 3 个，接着点就从 4 开始（用户反馈的那张图）。
        撤销栈只能从尾部弹，所以重编号既修好计数，也顺手抹平任何空洞。
        """
        n = 0
        for item in self._items:
            if item["kind"] == "number":
                n += 1
                item["n"] = n
        self._number = n + 1

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
        # 拖动到一半被快捷键结束时也要把候选选区定下来，别拿旧框出图
        if self._adjust is not None:
            self._end_adjust(commit=True)
        # 万一从别处（托盘 / 快捷键）在长图模式里结束了截图，先收干净会话
        if self._long_active:
            self._end_long_mode(resume=False)
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
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            return
        crop = self._compose_base()
        img = pixmap_to_pil(crop)
        self._busy = True
        self._toolbar.set_busy(True)
        self._toast("正在识别文字…")
        self._ocr_worker = _OcrTextWorker(img, self._cfg)
        self._ocr_worker.resultReady.connect(self._on_ocr_done)
        self._ocr_worker.finished.connect(self._ocr_thread_done)
        self._ocr_worker.start()

    def _ocr_thread_done(self):
        """线程无论如何收工，工具条都不能一直锁在 busy 状态。"""
        if self._busy:
            self._busy = False
            if self._toolbar is not None:
                self._toolbar.set_busy(False)

    def _on_ocr_done(self, text):
        self._ocr_thread_done()
        # 用户可能在识别途中点了完成 / 取消，别把面板弹回已关闭的编辑器
        if self._result is not None or not self.isVisible():
            return
        if self._ocr_panel is None:
            self._ocr_panel = OcrTextPanel(text)
        else:
            self._ocr_panel.set_text(text)
        g = self._sel_global()
        x = max(self._geom.left() + 8,
                min(g.center().x() - self._ocr_panel.width() // 2,
                    self._geom.right() - self._ocr_panel.width() - 8))
        y = max(self._geom.top() + 8, g.top() + 16)
        self._ocr_panel.move(x, y)
        self._show_child(self._ocr_panel)
        self._raise_children()

    # ---------- 截长图（手动滚轮）----------
    def _run_long(self):
        """进入手动长图模式：滚动交给用户，程序只负责采样 + 拼接。"""
        if getattr(self, "_is_reedit", False):
            self._toast("二次编辑里没有可滚动的原窗口，截长图请在首次截图时用")
            return
        if self._long_active or self._busy or self._result is not None:
            return
        from .long_capture import (
            SAMPLE_MS, LongCaptureHUD, ManualLongCapture,
        )

        hud = LongCaptureHUD()
        hud.finishClicked.connect(self._finish_long)
        hud.cancelClicked.connect(self._cancel_long)
        overlap = hud.place_outside(self._sel_global(), self._geom)

        self._busy = True
        if self._toolbar is not None:
            self._toolbar.set_busy(True)
        self._hide_children()
        self.hide()
        self._settle(160)          # 等编辑器真的从屏幕上消失，别把它拍进去

        factory = getattr(self, "_long_factory", None)
        session = (factory(self._sel_phys) if factory is not None
                   else ManualLongCapture(self._sel_phys))
        try:
            session.begin()
        except Exception as exc:
            session.release()
            hud.hide()
            hud.deleteLater()
            self._busy = False
            if self._toolbar is not None:
                self._toolbar.set_busy(False)
            self._resume()
            self._toast("截长图没能启动：%s" % exc)
            return

        self._long_session = session
        self._long_hud = hud
        self._long_overlap = overlap
        self._long_active = True
        # HUD 是独立顶层窗口，不能走 _show_child / _pin：编辑器此刻是隐藏的，
        # Windows 会把挂在它名下的 owned window 一起藏掉。
        hud.show()
        hud.raise_()
        hud.set_status(session.width, session.height, 0)
        self._long_timer = QTimer(self)
        self._long_timer.setInterval(SAMPLE_MS)
        self._long_timer.timeout.connect(self._poll_long)
        self._long_timer.start()

    def _poll_long(self):
        s, hud = self._long_session, self._long_hud
        if s is None or hud is None:
            return
        # 极少数「选区几乎占满整屏」的情况，HUD 只能压在选区上；
        # 抓帧那一瞬间把它藏掉，免得控制条被拼进长图里。
        hide_hud = self._long_overlap and hud.isVisible()
        if hide_hud:
            hud.hide()
            QApplication.processEvents()
        try:
            s.poll()
        except Exception as exc:
            self._end_long_mode()
            self._toast("截长图中断：%s" % exc)
            return
        finally:
            if hide_hud and self._long_hud is not None:
                hud.show()
                hud.raise_()
        hud.set_status(s.width, s.height, s.segments, s.limited, s.gained)

    def _finish_long(self):
        s = self._long_session
        if s is None:
            return
        try:
            img, segs, _gained = s.finish()
        except Exception as exc:
            self._end_long_mode()
            self._toast("截长图失败：%s" % exc)
            return
        self._end_long_mode()
        if segs == 0:
            self._toast("没检测到滚动：把鼠标留在截图区域里，慢慢向下滚滚轮")
            return
        self._long_pm = _pil_to_pixmap(img)
        self._items.clear()
        self._renumber()
        self._rebuild_mosaic()
        if self._toolbar is not None:
            self._toolbar.set_undo_enabled(False)
        self._options.hide()
        self._toast("长图 %d×%d · %d 段 · 可直接完成 / 保存"
                    % (img.width, img.height, segs))
        self.update()

    def _cancel_long(self):
        if not self._long_active:
            return
        self._end_long_mode()
        self._toast("已取消截长图，回到标注")

    def _end_long_mode(self, resume=True):
        """收掉 timer / HUD / 会话并恢复编辑态；cleanup 时 resume=False。"""
        if self._long_timer is not None:
            self._long_timer.stop()
            self._long_timer = None
        if self._long_session is not None:
            self._long_session.release()
            self._long_session = None
        if self._long_hud is not None:
            self._long_hud.hide()
            self._long_hud.deleteLater()
            self._long_hud = None
        self._long_active = False
        self._long_overlap = False
        self._busy = False
        if self._toolbar is not None:
            self._toolbar.set_busy(False)
        if resume:
            self._resume()

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

        active = (self._adjust["handle"]
                  if self._adjust is not None
                  and self._adjust["mode"] == "resize" else None)
        draw_selection_frame(p, QRectF(s).adjusted(0.5, 0.5, -0.5, -0.5),
                             width=2.6, grips=self._can_adjust(),
                             active_grip=active, grip_scale=self._zoom)
        if self._adjust is not None:
            self._paint_adjust_size(p)
        self._paint_tip(p)

    def _paint_tip(self, p):
        keys = ""
        if getattr(self, "_is_reedit", False):
            label = "继续标注   ·   Ctrl+Z 撤销   ·   Enter 写回原贴图   ·   Esc 取消"
        elif self._long_pm is not None:
            label = "长图已生成 · 点 ✔ 完成 / ⬇ 保存"
        else:
            label, keys = TIP_MAIN, TIP_KEYS
        f = QFont()
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        w = max(fm.horizontalAdvance(label), fm.horizontalAdvance(keys)) + 28
        lh = fm.height() + 3
        h = lh * (2 if keys else 1) + 12
        x = max(4, min(self._sel.left(), self.width() - w - 4))
        y = self._sel.top() - h - 6
        if y < 4:
            y = min(self._sel.bottom() + 6, self.height() - h - 4)
        accent = brand_qcolor()
        rim = QColor(accent)
        rim.setAlpha(90)
        p.setPen(QPen(rim, 1.0))
        p.setBrush(QColor(18, 20, 26, 222))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        # 小圆点只跟第一行对齐，别孤零零落在两行中间
        p.drawEllipse(QRectF(x + 8, y + 6 + lh / 2 - 2, 4, 4))
        p.setPen(QColor(235, 245, 255))
        p.drawText(QRectF(x + 14, y + 6, w - 14, lh),
                   Qt.AlignCenter, label)
        if keys:
            p.setPen(QColor(186, 199, 216))
            p.drawText(QRectF(x + 14, y + 6 + lh, w - 14, lh),
                       Qt.AlignCenter, keys)

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

    # ---------- 选区调整（V4.9.3）----------
    # 框选松开后立刻进入编辑态，框一直在屏幕上，所以「二次调整」就放在
    # 这里，不需要用户多点一步「确认」。三种手势：
    #   8 个白点    -> 改大小（四角同时改两条边）
    #   边框窄带    -> 整体移动；框内 Alt+拖动同样可以移动
    #   选区外拖动  -> 重新框一个选区，不用取消重来
    # 剩下的框内区域仍然归标注工具，两边不打架。
    def _can_adjust(self) -> bool:
        # 二次编辑的底图是深色编辑台，改选区会截到台面；长图 / 忙碌 /
        # 已出结果时也不该再动裁切框。
        return (not getattr(self, "_is_reedit", False)
                and self._long_pm is None and not self._long_active
                and not self._busy and self._result is None)

    def _hit(self, pt):
        if not self._can_adjust():
            return None, ""
        z = self._zoom
        return hit_selection(QPointF(pt), self._sel,
                             radius=GRIP_HIT * z, band=EDGE_BAND * z)

    def _min_sel(self):
        return MIN_SELECTION * self._zoom

    def _sync_selection(self, place: bool = True):
        """逻辑选区 -> 物理裁切框，并让工具条 / 选项条跟着新选区摆位。

        拖动过程也重摆：只 move 顶层窗口、不改 Z 序，比「松开后突然跳
        一下」顺眼得多，也不会把鼠标隐式捕获交给工具条。
        """
        s = self._sel
        gx = s.left() + self._geom.left()
        gy = s.top() + self._geom.top()
        px, py, pw, ph = to_physical(gx, gy, s.width(), s.height())
        self._sel_phys = Rect(px, py, pw, ph)
        self._crop = QRect(round(px - self._full.x),
                           round(py - self._full.y), pw, ph)
        if place and self._toolbar is not None:
            self._place_children()
        self.update()

    def _tool_cursor(self):
        return (Qt.PointingHandCursor if self._tool in CLICK_TOOLS
                else Qt.CrossCursor)

    def _update_cursor(self, pt):
        a = self._adjust
        if a is not None:
            mode, handle = a["mode"], a["handle"]
            if mode == "resize":
                cur = _RESIZE_CURSORS.get(handle, Qt.CrossCursor)
            elif mode == "move":
                cur = Qt.SizeAllCursor
            else:
                cur = Qt.CrossCursor
            kind = "%s:%s" % (mode, handle)
        else:
            mode, handle = self._hit(pt)
            if mode == "resize":
                kind, cur = "resize:" + handle, _RESIZE_CURSORS[handle]
            elif mode == "move":
                kind, cur = "move:", Qt.SizeAllCursor
            elif self._can_adjust() and not self._sel.contains(pt):
                kind, cur = "draw:", Qt.CrossCursor
            else:
                kind, cur = "tool:", self._tool_cursor()
        if kind != self._cursor_kind:
            self._cursor_kind = kind
            self.setCursor(cur)

    def _begin_adjust(self, pt, mode, handle=""):
        self._draft = None
        self._adjust = {
            "mode": mode, "handle": handle,
            "start": QRect(self._sel), "origin": QPoint(pt),
            "rect": QRect(self._sel),
        }
        self._apply_adjust(pt)

    def _apply_adjust(self, pt):
        a = self._adjust
        if a is None:
            return
        d = QPointF(pt - a["origin"])
        mode = a["mode"]
        if mode == "resize":
            rect = resize_selection(a["start"], a["handle"], d,
                                    self.rect(), self._min_sel())
        elif mode == "move":
            rect = clamp_selection(
                a["start"].translated(round(d.x()), round(d.y())),
                self.rect(), self._min_sel())
        else:                                   # draw：框外拖出新选区
            x1, y1 = a["origin"].x(), a["origin"].y()
            x2, y2 = pt.x(), pt.y()
            w, h = abs(x2 - x1), abs(y2 - y1)
            if w < self._min_sel() or h < self._min_sel():
                rect = QRect(a["start"])         # 手抖 / 只是点了下暗区
            else:
                rect = clamp_selection(
                    QRect(min(x1, x2), min(y1, y2), w, h),
                    self.rect(), self._min_sel())
        a["rect"] = rect
        self._sel = rect
        self._sync_selection(place=True)

    def _end_adjust(self, commit: bool = True):
        a = self._adjust
        if a is None:
            return
        self._adjust = None
        self._sel = QRect(a["rect"] if commit else a["start"])
        self._sync_selection()
        self._cursor_kind = ""                   # 强制下一次 move 重设光标

    def _paint_adjust_size(self, p: QPainter):
        s = self._sel
        label = "%d × %d" % (round(s.width() * self._scale),
                             round(s.height() * self._scale))
        f = QFont()
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        w, h = fm.horizontalAdvance(label) + 18, fm.height() + 8
        x = max(4, min(s.right() - w - 10, self.width() - w - 4))
        y = max(4, min(s.bottom() - h - 10, self.height() - h - 4))
        accent = brand_qcolor()
        rim = QColor(accent)
        rim.setAlpha(150)
        p.setPen(QPen(rim, 1.0))
        p.setBrush(QColor(18, 20, 26, 232))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        p.setPen(QColor(240, 248, 255))
        p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, label)

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

        if self._can_adjust():
            mode, handle = self._hit(pt)
            if mode is None and event.modifiers() & Qt.AltModifier \
                    and self._sel.contains(pt):
                mode = "move"                    # 框内任意位置也能整框拖动
            elif mode is None and not self._sel.contains(pt):
                mode = "draw"                    # 暗区按下 = 重新框选
            if mode is not None:
                self._begin_adjust(pt, mode, handle)
                self._update_cursor(pt)
                event.accept()
                return

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
        pt = _pos(event)
        if self._adjust is not None:
            self._apply_adjust(pt)
            return
        if self._draft is None:
            self._update_cursor(pt)
            return
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
        if event.button() != Qt.LeftButton:
            return
        if self._adjust is not None:
            a = self._adjust
            changed = a["rect"] != a["start"]
            self._end_adjust(commit=True)
            if changed:
                self._toast("选区 %d × %d  ·  可继续标注或再调整"
                            % (round(self._sel.width() * self._scale),
                               round(self._sel.height() * self._scale)))
            self._update_cursor(_pos(event))
            return
        if self._draft is None:
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
            if self._adjust is not None:
                self._end_adjust(commit=False)
                self._toast("已还原选区")
                return
            if self._long_active:
                self._cancel_long()
                return
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
        if not (self._long_pm is not None or not self._items):
            p = QPainter(out)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setViewport(QRect(0, 0, out.width(), out.height()))
            p.setWindow(self._sel)
            self._draw_annotations(p)
            p.end()
        # 二次编辑里大图会在编辑台上等比缩小，导出时缩回原始分辨率，
        # 回写贴图才不会悄悄改掉尺寸
        size = getattr(self, "_export_size", None)
        if size and (out.width(), out.height()) != size:
            out = out.scaled(size[0], size[1], Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation)
        return out
