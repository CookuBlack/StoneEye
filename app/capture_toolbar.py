# -*- coding: utf-8 -*-
"""截图编辑态的浮动工具栏 + 颜色/线宽选项条 + 表情选择面板。

外观和控制面板共用 `ShellWindow`：真圆角、自绘投影、顶边高光，
所以工具条看起来是「浮在截图上」而不是贴了一块深色矩形。

三组按钮之间各有一条 1px 灰色分隔线，顺序与微信 / QQ 截图一致。
工具栏会被摆到选区正下方；下方放不下就翻到上方，仍放不下就贴屏幕底。
"""
from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QGridLayout, QHBoxLayout, QPushButton, QWidget,
)

from .capture_icons import (
    ACTIONS, ADVANCED, ALL_IDS, DRAW_TOOLS, ICON_ACTIVE, ICON_COLOR,
    ICON_HOVER, TOOLS, draw_icon,
)
from .ui_shell import ShellWindow
from .ui_theme import BG_RAISED

BTN = 34
ICON = 22
SEP_H = 20
COLORS = (
    ("#F2645B", "红"), ("#F0A020", "橙"), ("#F2D64B", "黄"),
    ("#4FD07A", "绿"), ("#4E8DFF", "蓝"), ("#B57BFF", "紫"),
    ("#FFFFFF", "白"), ("#181818", "黑"),
)
STICKERS = (
    "😀", "😂", "😍", "😎", "🤔", "😭", "😡", "👍", "👏", "🙏",
    "❤️", "🔥", "⭐", "✨", "💡", "❓", "❗", "✅", "❌", "💯",
)

# 这几个浮窗只提供按钮，不需要键盘焦点。设成不抢焦点后，点击它们不会
# 触发一次前台窗口切换 —— 既保住编辑器的 Esc / Ctrl+Z 快捷键，也少一次
# 让 Windows 重排 topmost Z 序的机会（V4.8 工具条「点一下就消失」的根源）。
# OCR 结果面板要能选中文本复制，所以**不**用这组 flags。
FLOAT_FLAGS = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
               | Qt.WindowDoesNotAcceptFocus)


class ToolButton(QAbstractButton):
    """自绘图标按钮：悬停有一层淡底，选中工具再叠一层亮底。"""

    def __init__(self, ident, tip, scale, parent=None):
        super().__init__(parent)
        self.ident = ident
        self._scale = scale
        self._hover = False
        self.setToolTip(tip)
        self.setCheckable(ident in tuple(i for i, _, _ in TOOLS + ADVANCED))
        self.setFixedSize(round(BTN * scale), round(BTN * scale))
        self.setCursor(Qt.PointingHandCursor)
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
        if self._hover and not self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 18))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)
        col = ICON_ACTIVE if self.isChecked() else (
            ICON_HOVER if self._hover else ICON_COLOR)
        side = ICON * self._scale
        pad = (self.width() - side) / 2.0
        draw_icon(p, self.ident, QRectF(pad, pad, side, side),
                  col, self.isChecked(), self.isEnabled())
        p.end()


class _Separator(QWidget):
    def __init__(self, scale, parent=None):
        super().__init__(parent)
        self.setFixedSize(1, round(SEP_H * scale))
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect().adjusted(0, 2, 0, -2), QColor("#3C3C3C"))


class CaptureToolbar(ShellWindow):
    """16 键主工具条。"""

    RADIUS = 12
    PAD = 13
    BODY_TOP = "#282828"
    BODY_MID = "#212121"
    BODY_BOTTOM = "#1C1C1C"

    toolSelected = Signal(str)
    actionTriggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, FLOAT_FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._buttons = {}
        self._active = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(self._spad, self._spad, self._spad, self._spad)
        lay.setSpacing(round(2 * self._scale))

        groups = (TOOLS, ADVANCED, ACTIONS)
        for gi, group in enumerate(groups):
            if gi:
                lay.addSpacing(round(9 * self._scale))
                lay.addWidget(_Separator(self._scale))
                lay.addSpacing(round(9 * self._scale))
            for ident, _name, tip in group:
                b = ToolButton(ident, tip, self._scale)
                lay.addWidget(b)
                self._buttons[ident] = b
                if ident in tuple(i for i, _, _ in TOOLS + ADVANCED):
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
    def __init__(self, color, size, parent=None, ring=False):
        super().__init__(parent)
        self.color = QColor(color)
        self.ring = ring
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isChecked():
            p.setPen(QPen(QColor("#FFFFFF"), 1.6))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 45), 1.0))
        if self.ring:
            p.setBrush(Qt.NoBrush)
        else:
            p.setBrush(self.color)
        p.drawEllipse(r)


class OptionsBar(ShellWindow):
    """颜色 / 线宽 / 字号；只在选中绘制或文字工具时出现。"""

    RADIUS = 11
    PAD = 11
    BODY_TOP = "#282828"
    BODY_MID = "#212121"
    BODY_BOTTOM = "#1C1C1C"

    colorChanged = Signal(str)
    widthChanged = Signal(int)
    sizeChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent, FLOAT_FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.color = COLORS[0][0]
        self.pen_width = 3
        self.font_size = 20
        self._colors = []
        self._widths = []
        self._size_btns = []

        lay = QHBoxLayout(self)
        lay.setContentsMargins(self._spad, self._spad, self._spad, self._spad)
        lay.setSpacing(round(4 * self._scale))
        sz = round(24 * self._scale)

        for color, name in COLORS:
            b = _Swatch(color, sz)
            b.setToolTip(name)
            b.clicked.connect(lambda checked=False, c=color: self._pick_color(c))
            lay.addWidget(b)
            self._colors.append((color, b))

        lay.addWidget(_Separator(self._scale))
        for w in (2, 4, 7):
            b = _Swatch("#FFFFFF", sz, ring=True)
            b.setToolTip("线宽 %d" % w)
            b.clicked.connect(lambda checked=False, v=w: self._pick_width(v))
            lay.addWidget(b)
            self._widths.append((w, b))

        lay.addWidget(_Separator(self._scale))
        for text, delta in (("A-", -2), ("A+", 2)):
            b = QPushButton(text)
            b.setFixedSize(round(30 * self._scale), sz)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:%s;color:#DDD;border:1px solid #3C3C3C;"
                "border-radius:6px;font-weight:600;}"
                "QPushButton:hover{background:#333;}" % BG_RAISED)
            b.clicked.connect(lambda checked=False, d=delta:
                              self._bump_size(d))
            lay.addWidget(b)
            self._size_btns.append(b)

        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.ensure_rim()
        self._pick_color(COLORS[0][0])
        self._pick_width(3)

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
        is_text = tool == "text"
        for b in self._size_btns:
            b.setVisible(is_text)
        for _w, b in self._widths:
            b.setVisible(not is_text)
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


class StickerPicker(ShellWindow):
    """点一下挑表情，之后在选区里点哪儿就贴哪儿。"""

    RADIUS = 12
    PAD = 12
    BODY_TOP = "#282828"
    BODY_MID = "#212121"
    BODY_BOTTOM = "#1C1C1C"

    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, FLOAT_FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        lay = QGridLayout(self)
        lay.setContentsMargins(self._spad, self._spad, self._spad, self._spad)
        lay.setSpacing(round(3 * self._scale))
        cell = round(34 * self._scale)
        f = QFont("Segoe UI Emoji")
        f.setPixelSize(round(21 * self._scale))
        for idx, ch in enumerate(STICKERS):
            b = QPushButton(ch)
            b.setFixedSize(cell, cell)
            b.setFont(f)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:0;border-radius:6px;}"
                "QPushButton:hover{background:#3A3A3A;}")
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
