# -*- coding: utf-8 -*-
"""截图编辑态的浮动工具栏 + 颜色/线宽选项条 + 表情选择面板。

外观和控制面板共用 `ShellWindow`：真圆角、自绘投影、顶边高光，
所以工具条看起来是「浮在截图上」而不是贴了一块深色矩形。

V4.9.1 重做视觉：冷调玻璃底板 + 顶部品牌色渐隐高光 + 按钮 hover /
选中 / 点击脉冲的平滑补间；状态底全部由按钮自绘，图标只负责线条。

按钮分两类（V4.9.1 修）：`TOOL_IDS` 是会切换绘制模式的复选工具，
其余一律是「按一下执行一次」的动作。早期把 ocr / long 也做成了复选
工具，点击只发 toolSelected，编辑器里的 _run_ocr / _run_long 永远收不到，
表现为「提取文字」和「截长图」两个按钮点了没反应。

三组按钮之间各有一条渐隐分隔线，顺序与微信 / QQ 截图一致。
工具栏会被摆到选区正下方；下方放不下就翻到上方，仍放不下就贴屏幕底。
"""
from PySide6.QtCore import (
    QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton, QGridLayout, QHBoxLayout, QPushButton, QWidget,
)

from .brand import brand_hex, brand_qcolor
from .capture_icons import (
    ACTIONS, ADVANCED, ALL_IDS, ICON_CANCEL, ICON_DISABLED, ICON_DONE,
    TOOLS, draw_icon,
)
from .ui_shell import ShellWindow

BTN = 36
ICON = 20
SEP_H = 20
CONTENT_PAD = 7          # 底板内沿到按钮的留白
GROUP_GAP = 10

# 冷调蓝灰，和图标主色（偏蓝）同一个色系；纯黑底会让白色图标显得毛躁
SURFACE_TOP = "#323842"
SURFACE_MID = "#252A32"
SURFACE_BOTTOM = "#1B1F26"
ICON_BASE = QColor("#CBD2DE")
ICON_HOVER = QColor("#FFFFFF")

COLORS = (
    ("#F2645B", "红"), ("#F0A020", "橙"), ("#F2D64B", "黄"),
    ("#4FD07A", "绿"), ("#4E8DFF", "蓝"), ("#B57BFF", "紫"),
    ("#FFFFFF", "白"), ("#181818", "黑"),
)
STICKERS = (
    "😀", "😂", "😍", "😎", "🤔", "😭", "😡", "👍", "👏", "🙏",
    "❤️", "🔥", "⭐", "✨", "💡", "❓", "❗", "✅", "❌", "💯",
)

# 只有这些是「选中后一直在用」的工具；ADVANCED 里的 ocr / long 是动作。
TOOL_IDS = frozenset(i for i, _, _ in TOOLS) | {"number"}
ACTION_IDS = frozenset(ALL_IDS) - TOOL_IDS

# 这几个浮窗只提供按钮，不需要键盘焦点。设成不抢焦点后，点击它们不会
# 触发一次前台窗口切换 —— 既保住编辑器的 Esc / Ctrl+Z 快捷键，也少一次
# 让 Windows 重排 topmost Z 序的机会（V4.8 工具条「点一下就消失」的根源）。
# OCR 结果面板要能选中文本复制，所以**不**用这组 flags。
FLOAT_FLAGS = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
               | Qt.WindowDoesNotAcceptFocus)


def _alpha(color: QColor, a: int) -> QColor:
    q = QColor(color)
    q.setAlpha(max(0, min(255, a)))
    return q


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        255,
    )


class ToolSurface(ShellWindow):
    """工具条 / 选项条 / 表情板 / OCR 面板共用的一张「冷调玻璃」底板。

    比控制面板更亮一点：截图编辑态是盖在任意底图上的，底板太黑会
    和浅色照片糊成一片；顶缘加一道品牌色渐隐高光，让长条有「头」。
    """

    RADIUS = 14
    PAD = 14
    BODY_TOP = SURFACE_TOP
    BODY_MID = SURFACE_MID
    BODY_BOTTOM = SURFACE_BOTTOM

    def content_margins(self) -> int:
        """布局边距 = 投影留白 + 底板内留白。"""
        return self._spad + self._px(CONTENT_PAD)

    def paint_beyond_body(self, painter: QPainter, body: QRectF):
        radius = float(self._px(self.RADIUS))
        path = QPainterPath()
        path.addRoundedRect(body, radius, radius)
        painter.save()
        painter.setClipPath(path)

        # 上半部一层极淡的玻璃反光，纯渐变底板会显得像塑料片
        gloss = QLinearGradient(
            body.topLeft(),
            QPointF(body.left(), body.top() + body.height() * 0.55))
        gloss.setColorAt(0.0, QColor(255, 255, 255, 17))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(body, QBrush(gloss))

        # 顶缘品牌色高光：中间亮、往两端淡出，比整条白线更有辨识度
        accent = brand_qcolor()
        line = QLinearGradient(body.left(), body.top(),
                               body.right(), body.top())
        line.setColorAt(0.00, _alpha(accent, 0))
        line.setColorAt(0.22, _alpha(accent, 48))
        line.setColorAt(0.50, _alpha(accent, 165))
        line.setColorAt(0.78, _alpha(accent, 48))
        line.setColorAt(1.00, _alpha(accent, 0))
        painter.setPen(QPen(QBrush(line), 1.4))
        painter.drawLine(QPointF(body.left() + radius, body.top() + 1.3),
                         QPointF(body.right() - radius, body.top() + 1.3))

        # 底边一道暗线，给浮层一点厚度
        painter.setPen(QPen(QColor(0, 0, 0, 72), 1.0))
        painter.drawLine(QPointF(body.left() + radius, body.bottom() - 0.7),
                         QPointF(body.right() - radius, body.bottom() - 0.7))
        painter.restore()


class ToolButton(QAbstractButton):
    """自绘图标按钮。

    - hover / 离开：140ms 补间，淡底和图标一起由冷白渐变到纯白；
    - 选中工具：品牌色胶囊 + 描边 + 底部指示点，图标变品牌色；
    - 动作按钮：点完有一道 340ms 的高亮脉冲，明确「这一下生效了」；
    - 取消 / 完成：常驻一层极淡的红 / 绿底，hover 时加深。
    """

    def __init__(self, ident, tip, scale, parent=None):
        super().__init__(parent)
        self.ident = ident
        self._scale = scale
        self._fade = 0.0
        self._flash = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(140)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._set_fade)
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(340)
        self._flash_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._flash_anim.valueChanged.connect(self._set_flash)
        self.setToolTip(tip)
        self.setCheckable(ident in TOOL_IDS)
        self.setFixedSize(round(BTN * scale), round(BTN * scale))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.clicked.connect(self._on_clicked)

    def _set_fade(self, value):
        self._fade = float(value)
        self.update()

    def _set_flash(self, value):
        self._flash = float(value)
        self.update()

    def _animate(self, anim, value, start):
        anim.stop()
        anim.setStartValue(float(start))
        anim.setEndValue(float(value))
        anim.start()

    def _on_clicked(self, _checked=False):
        if self.isCheckable():
            return
        self._animate(self._flash_anim, 0.0, 1.0)

    def enterEvent(self, _e):
        self._animate(self._hover_anim, 1.0, self._fade)

    def leaveEvent(self, _e):
        self._animate(self._hover_anim, 0.0, self._fade)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        accent = brand_qcolor()
        enabled = self.isEnabled()
        pressed = self.isDown() and enabled
        hover = max(self._fade, 1.0 if pressed else 0.0)
        pulse = max(hover, self._flash)

        if self.ident in ("cancel", "done") and enabled:
            tint = ICON_CANCEL if self.ident == "cancel" else ICON_DONE
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(tint, 20 + int(42 * pulse)))
            p.drawRoundedRect(r, 9, 9)

        if self.isChecked() and enabled:
            p.setBrush(_alpha(accent, 44))
            p.setPen(QPen(_alpha(accent, 125), 1.0))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(accent, 230))
            d = 3.0 * self._scale
            p.drawEllipse(QRectF(self.width() / 2.0 - d / 2.0,
                                 self.height() - 5.5 * self._scale, d, d))
        elif enabled and pulse > 0.001:
            p.setBrush(QColor(255, 255, 255, int(28 * pulse)))
            p.setPen(QPen(QColor(255, 255, 255, int(38 * pulse)), 1.0))
            p.drawRoundedRect(r, 9, 9)

        if not enabled:
            col = ICON_DISABLED
        elif self.ident == "cancel":
            col = ICON_CANCEL
        elif self.ident == "done":
            col = ICON_DONE
        elif self.isChecked():
            col = accent
        else:
            col = _mix(ICON_BASE, ICON_HOVER, pulse)

        side = ICON * self._scale
        pad = (self.width() - side) / 2.0
        draw_icon(p, self.ident, QRectF(pad, pad, side, side),
                  col, self.isChecked() and enabled, enabled)
        p.end()


class _Separator(QWidget):
    """1px 竖向渐隐线：两端融进底板，中间才显形，比硬直线干净。"""

    def __init__(self, scale, parent=None):
        super().__init__(parent)
        self.setFixedSize(1, round(SEP_H * scale))
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(self.rect())
        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        grad.setColorAt(0.5, QColor(255, 255, 255, 62))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(r, QBrush(grad))
        p.end()


class CaptureToolbar(ToolSurface):
    """16 键主工具条。"""

    toolSelected = Signal(str)
    actionTriggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, FLOAT_FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._buttons = {}
        self._active = None

        lay = QHBoxLayout(self)
        m = self.content_margins()
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(self._px(3))

        groups = (TOOLS, ADVANCED, ACTIONS)
        for gi, group in enumerate(groups):
            if gi:
                lay.addSpacing(self._px(GROUP_GAP))
                lay.addWidget(_Separator(self._scale))
                lay.addSpacing(self._px(GROUP_GAP))
            for ident, _name, tip in group:
                b = ToolButton(ident, tip, self._scale)
                lay.addWidget(b)
                self._buttons[ident] = b
                if ident in TOOL_IDS:
                    b.clicked.connect(lambda checked=False, k=ident:
                                      self._on_tool(k))
                else:
                    b.clicked.connect(lambda checked=False, k=ident:
                                      self.actionTriggered.emit(k))

        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.ensure_rim()

    def _on_tool(self, ident):
        self.set_tool(ident)
        self.toolSelected.emit(ident)

    def set_tool(self, ident):
        self._active = ident
        for key, btn in self._buttons.items():
            if btn.isCheckable():
                btn.setChecked(key == ident)

    def active_tool(self):
        return self._active

    def set_undo_enabled(self, ok: bool):
        self._buttons["undo"].setEnabled(bool(ok))

    def set_busy(self, busy: bool):
        for ident in ("ocr", "long", "save", "done", "pin", "share"):
            self._buttons[ident].setEnabled(not busy)

    def place_for(self, sel: QRect, screen: QRect):
        """sel / screen 都是全局逻辑坐标。优先选区正下方，其次上方。"""
        gap = round(10 * self._scale)
        x = sel.center().x() - self.width() // 2
        y = sel.bottom() + gap
        if y + self.height() > screen.bottom() - gap:
            y = sel.top() - self.height() - gap
        if y < screen.top() + gap:
            y = screen.bottom() - self.height() - gap
        x = max(screen.left() + gap,
                min(x, screen.right() - self.width() - gap))
        self.move(x, y)
        return QRect(x, y, self.width(), self.height())


class _Swatch(QAbstractButton):
    """颜色点：选中时套一圈品牌色细环，hover 有淡胶囊底。"""

    def __init__(self, color, size, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._hover or self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 28 if self._hover else 18))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 7, 7)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        if self.isChecked():
            outer = r.adjusted(-2.2, -2.2, 2.2, 2.2)
            p.setPen(QPen(_alpha(brand_qcolor(), 215), 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(outer)
            r = r.adjusted(0.8, 0.8, -0.8, -0.8)
        p.setPen(QPen(QColor(255, 255, 255, 55), 1.0))
        p.setBrush(self.color)
        p.drawEllipse(r)
        p.end()


class _WidthSwatch(QAbstractButton):
    """线宽：直接画一根对应粗细的横线，比三个空心圆直观得多。"""

    def __init__(self, width, size, parent=None):
        super().__init__(parent)
        self.width_value = width
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked():
            p.setPen(QPen(_alpha(brand_qcolor(), 120), 1.0))
            p.setBrush(_alpha(brand_qcolor(), 40))
            p.drawRoundedRect(r, 7, 7)
        elif self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 28))
            p.drawRoundedRect(r, 7, 7)
        col = (brand_qcolor() if self.isChecked()
               else QColor("#FFFFFF") if self._hover else QColor("#D9E0EA"))
        p.setPen(QPen(col, float(self.width_value),
                      Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(r.left() + 6, r.center().y()),
                   QPointF(r.right() - 6, r.center().y()))
        p.end()


class OptionsBar(ToolSurface):
    """颜色 / 线宽 / 字号；只在选中绘制或文字工具时出现。"""

    colorChanged = Signal(str)
    widthChanged = Signal(int)
    sizeChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent, FLOAT_FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.color = COLORS[0][0]
        # 档位里没有 3，默认值必须落在某一档上，否则初始三档全都没选中
        self.pen_width = 4
        self.font_size = 20
        self._colors = []
        self._widths = []
        self._size_btns = []

        lay = QHBoxLayout(self)
        m = self.content_margins()
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(self._px(4))
        sz = self._px(24)

        for color, name in COLORS:
            b = _Swatch(color, sz)
            b.setToolTip(name)
            b.clicked.connect(lambda checked=False, c=color: self._pick_color(c))
            lay.addWidget(b)
            self._colors.append((color, b))

        lay.addSpacing(self._px(4))
        self._width_sep = _Separator(self._scale)
        lay.addWidget(self._width_sep)
        lay.addSpacing(self._px(4))
        for w in (2, 4, 7):
            b = _WidthSwatch(w, sz)
            b.setToolTip("线宽 %d" % w)
            b.clicked.connect(lambda checked=False, v=w: self._pick_width(v))
            lay.addWidget(b)
            self._widths.append((w, b))

        lay.addSpacing(self._px(4))
        self._size_sep = _Separator(self._scale)
        lay.addWidget(self._size_sep)
        lay.addSpacing(self._px(4))
        accent = brand_hex()
        for text, delta in (("A−", -2), ("A+", 2)):
            b = QPushButton(text)
            b.setFixedSize(self._px(30), sz)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,16);color:#DCE3EE;"
                "border:1px solid rgba(255,255,255,28);border-radius:7px;"
                "font-weight:700;font-size:13px;}"
                "QPushButton:hover{background:rgba(255,255,255,46);color:#FFFFFF;}"
                "QPushButton:pressed{background:%s;color:#FFFFFF;}" % accent)
            b.clicked.connect(lambda checked=False, d=delta:
                              self._bump_size(d))
            lay.addWidget(b)
            self._size_btns.append(b)

        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.ensure_rim()
        self._pick_color(COLORS[0][0])
        self._pick_width(self.pen_width)

    def _pick_color(self, color):
        self.color = color
        for c, b in self._colors:
            b.setChecked(c == color)
        self.colorChanged.emit(color)

    def _pick_width(self, width):
        self.pen_width = width
        for w, b in self._widths:
            b.setChecked(w == width)
        self.widthChanged.emit(width)

    def _bump_size(self, delta):
        self.font_size = max(12, min(72, self.font_size + delta))
        self.sizeChanged.emit(self.font_size)

    def set_mode(self, tool: str):
        """文字工具只留「颜色 + 字号」，绘制工具只留「颜色 + 线宽」。

        两条分隔线跟着各自那组一起显隐，否则切到绘制模式时右边会挂
        一条没有内容可分的孤线。
        """
        is_text = tool == "text"
        for b in self._size_btns:
            b.setVisible(is_text)
        for _w, b in self._widths:
            b.setVisible(not is_text)
        self._width_sep.setVisible(not is_text)
        self._size_sep.setVisible(is_text)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())

    def place_above(self, toolbar: QRect, screen: QRect):
        gap = round(8 * self._scale)
        x = max(screen.left() + gap,
                min(toolbar.center().x() - self.width() // 2,
                    screen.right() - self.width() - gap))
        y = toolbar.top() - self.height() - gap
        if y < screen.top() + gap:
            y = toolbar.bottom() + gap
        self.move(x, y)


class StickerPicker(ToolSurface):
    """点一下挑表情，之后在选区里点哪儿就贴哪儿。"""

    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, FLOAT_FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        lay = QGridLayout(self)
        m = self.content_margins()
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(self._px(4))
        cell = self._px(34)
        f = QFont("Segoe UI Emoji")
        f.setPixelSize(self._px(21))
        accent = brand_qcolor()
        hover = "rgba(%d,%d,%d,46)" % (accent.red(), accent.green(),
                                       accent.blue())
        for idx, ch in enumerate(STICKERS):
            b = QPushButton(ch)
            b.setFixedSize(cell, cell)
            b.setFont(f)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:0;border-radius:9px;}"
                "QPushButton:hover{background:%s;}" % hover)
            b.clicked.connect(lambda checked=False, c=ch: self.picked.emit(c))
            lay.addWidget(b, idx // 5, idx % 5)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.ensure_rim()

    def place_above(self, toolbar: QRect, screen: QRect):
        gap = round(8 * self._scale)
        x = max(screen.left() + gap,
                min(toolbar.center().x() - self.width() // 2,
                    screen.right() - self.width() - gap))
        y = toolbar.top() - self.height() - gap
        if y < screen.top() + gap:
            y = toolbar.bottom() + gap
        self.move(x, y)
