# -*- coding: utf-8 -*-
"""控制面板用的小控件（V4.4 · 中性灰阶 + 线性图标 + 补间动画）。

整套思路对齐 WorkBuddy 桌面端：

  - **侧栏导航项** 图标 + 文字 + 可选计数，选中底色由侧栏统一画一条会
    滑动的胶囊（而不是每一项自己填色），切页时它从上一项滑到下一项；
  - **线性图标**全部用 QPainter 画（⊕ ↻ ⚙ 之类字符在微软雅黑里会缺字形，
    渲染成空心方框，而且 12px 下细节全糊，画路径反而更清晰）；
  - **胶囊开关**整行可点，灰底 = 关、白底 + 深色圆点 = 开，
    轨道和圆点的颜色跟着 0→1 的补间插值，不是硬切；
  - **分段选择**自绘轨道 + 会滑动的选中块，替代下拉框。

控件都不写死颜色，统一从 ui_theme 取，改一处就能整体换肤。
"""
import math

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSizeGrip, QVBoxLayout, QWidget,
)

from .ui_theme import (
    ACCENT, BD_CARD, BG_CARD, BG_TRACK, R_CARD, R_CTRL, R_SM,
    SEL_BG, SEL_BG_HOVER, TEXT, TEXT_2, TEXT_3,
)


def px(value: float, scale: float) -> int:
    return max(0, int(round(float(value) * scale)))


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """在两种颜色之间线性插值（t 会被夹到 0~1）。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
        int(round(a.alpha() + (b.alpha() - a.alpha()) * t)),
    )


class SilentGrip(QSizeGrip):
    """只负责拖拽缩放、不画默认点阵的握把（点阵和自绘斜线会打架）。"""

    def paintEvent(self, _event):
        pass


# ---------------------------------------------------------------- 线性图标
class LineIcon(QWidget):
    """16px 线性图标，全部按 1x1 归一化坐标绘制，任意 DPI 都锐利。"""

    def __init__(self, kind: str, scale: float = 1.0, size: float = 16.0,
                 color: str = TEXT_2, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._scale = scale
        self._size = float(size)
        self._color = QColor(color)
        self.setFixedSize(px(size, scale), px(size, scale))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_color(self, color):
        color = QColor(color)
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, _event):
        s = float(self.width())
        pen = QPen(self._color, max(1.0, 1.5 * self._scale))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        k = self._kind

        def L(x1, y1, x2, y2):
            p.drawLine(QPointF(x1 * s, y1 * s), QPointF(x2 * s, y2 * s))

        if k == "plus":
            p.drawEllipse(QRectF(0.09 * s, 0.09 * s, 0.82 * s, 0.82 * s))
            L(0.5, 0.29, 0.5, 0.71)
            L(0.29, 0.5, 0.71, 0.5)
        elif k == "refresh":
            path = QPainterPath()
            path.arcMoveTo(QRectF(0.16 * s, 0.16 * s, 0.68 * s, 0.68 * s), 55)
            path.arcTo(QRectF(0.16 * s, 0.16 * s, 0.68 * s, 0.68 * s), 55, 275)
            p.drawPath(path)
            head = QPainterPath()
            head.moveTo(0.55 * s, 0.05 * s)
            head.lineTo(0.86 * s, 0.19 * s)
            head.lineTo(0.60 * s, 0.38 * s)
            p.drawPath(head)
        elif k == "close":
            L(0.24, 0.24, 0.76, 0.76)
            L(0.76, 0.24, 0.24, 0.76)
        elif k == "grid":
            r = 3.2 * self._scale
            for cx, cy in ((0.27, 0.27), (0.73, 0.27), (0.27, 0.73), (0.73, 0.73)):
                p.drawRoundedRect(
                    QRectF((cx - 0.17) * s, (cy - 0.17) * s, 0.34 * s, 0.34 * s),
                    r, r)
        elif k == "display":
            p.drawRoundedRect(
                QRectF(0.08 * s, 0.15 * s, 0.84 * s, 0.6 * s),
                3.0 * self._scale, 3.0 * self._scale)
            L(0.22, 0.86, 0.78, 0.86)
        elif k == "tune":
            for y, knob in ((0.24, 0.34), (0.5, 0.66), (0.76, 0.44)):
                L(0.10, y, 0.90, y)
                p.setBrush(self._color)
                p.drawEllipse(QPointF(knob * s, y * s), 0.085 * s, 0.085 * s)
                p.setBrush(Qt.NoBrush)
        elif k == "list":
            for y in (0.24, 0.5, 0.76):
                p.setBrush(self._color)
                p.drawEllipse(QPointF(0.16 * s, y * s), 0.045 * s, 0.045 * s)
                p.setBrush(Qt.NoBrush)
                L(0.34, y, 0.88, y)
        elif k == "gear":
            path = QPainterPath()
            path.addEllipse(QRectF(0.34 * s, 0.34 * s, 0.32 * s, 0.32 * s))
            p.drawPath(path)
            for i in range(8):
                a = math.radians(i * 45)
                cx, cy = 0.5 * s, 0.5 * s
                r1, r2 = 0.30 * s, 0.44 * s
                p.drawLine(
                    QPointF(cx + math.cos(a) * r1, cy + math.sin(a) * r1),
                    QPointF(cx + math.cos(a) * r2, cy + math.sin(a) * r2))
        elif k == "folder":
            path = QPainterPath()
            path.moveTo(0.10 * s, 0.78 * s)
            path.lineTo(0.10 * s, 0.26 * s)
            path.lineTo(0.40 * s, 0.26 * s)
            path.lineTo(0.50 * s, 0.38 * s)
            path.lineTo(0.90 * s, 0.38 * s)
            path.lineTo(0.90 * s, 0.78 * s)
            path.closeSubpath()
            p.drawPath(path)
        elif k == "sparkle":
            path = QPainterPath()
            path.moveTo(0.5 * s, 0.08 * s)
            path.cubicTo(0.58 * s, 0.38 * s, 0.62 * s, 0.42 * s, 0.92 * s, 0.5 * s)
            path.cubicTo(0.62 * s, 0.58 * s, 0.58 * s, 0.62 * s, 0.5 * s, 0.92 * s)
            path.cubicTo(0.42 * s, 0.62 * s, 0.38 * s, 0.58 * s, 0.08 * s, 0.5 * s)
            path.cubicTo(0.38 * s, 0.42 * s, 0.42 * s, 0.38 * s, 0.5 * s, 0.08 * s)
            p.drawPath(path)
        elif k == "copy":
            p.drawRoundedRect(
                QRectF(0.10 * s, 0.10 * s, 0.52 * s, 0.52 * s),
                2.5 * self._scale, 2.5 * self._scale)
            L(0.36, 0.80, 0.86, 0.80)
            L(0.86, 0.80, 0.86, 0.38)
        elif k == "chevron":
            L(0.40, 0.24, 0.66, 0.5)
            L(0.66, 0.5, 0.40, 0.76)
        elif k == "dot":
            p.setBrush(self._color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(0.32 * s, 0.32 * s, 0.36 * s, 0.36 * s))
        p.end()


class Chevron(LineIcon):
    """左对齐兼容旧调用的右尖括号。"""

    def __init__(self, scale: float = 1.0, parent=None):
        super().__init__("chevron", scale, 10, TEXT_3, parent)


# ---------------------------------------------------------------- 卡片
class Card(QFrame):
    """自绘圆角卡片：底色 + 1px 描边 + 顶部一线极淡高光。

    高光那一条是把"卡片浮在内容区上"这件事说清楚的关键 ——
    只靠 1px 描边的话，在深色屏上几乎看不出层级。
    """

    def __init__(self, scale: float = 1.0, parent=None):
        super().__init__(parent)
        self._scale = scale
        self.setObjectName("card")

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = px(R_CARD, self._scale)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        p.fillPath(path, QColor(BG_CARD))

        p.save()
        p.setClipPath(path)
        top = QLinearGradient(0, rect.top(), 0, rect.top() + px(56, self._scale))
        top.setColorAt(0.0, QColor(255, 255, 255, 12))
        top.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(rect.left(), rect.top(), rect.width(),
                          px(56, self._scale)), top)
        p.restore()

        p.setPen(QPen(QColor(BD_CARD), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()


# ---------------------------------------------------------------- 侧栏项
class NavItem(QWidget):
    """侧栏一行：图标 + 文字 +（可选）右侧计数/箭头，整行可点。

    注意选中底色不在这里画 —— 由 _Sidebar 统一画一条可以滑动的胶囊，
    这样切页时能看见"高亮从上一项滑到这一项"。这里只负责悬停反馈。
    """

    clicked = Signal()

    def __init__(self, kind: str, label: str, scale: float = 1.0,
                 value: str = "", arrow: bool = False, parent=None):
        super().__init__(parent)
        self._scale = scale
        self._active = False
        self._accent = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(px(34, scale))
        self.setAttribute(Qt.WA_Hover, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(10, scale), 0, px(8, scale), 0)
        lay.setSpacing(px(10, scale))

        self.icon = LineIcon(kind, scale, 16, TEXT_2)
        self._label = QLabel(label)
        self._label.setObjectName("navtitle")
        self._label.setStyleSheet(
            f"color:{TEXT_2}; font-size:{px(13, scale)}px; background:transparent;")
        self._value = QLabel(value)
        self._value.setObjectName("navvalue")
        self._chev = LineIcon("chevron", scale, 9, TEXT_3) if arrow else None

        lay.addWidget(self.icon)
        lay.addWidget(self._label, 1)
        # 计数标签一律进布局、靠可见性控制：只在构造时 value 非空才 addWidget
        # 的话，以后再 set_value() 就是往一个不在布局里的 label 写文字 ——
        # 「识别规则」那行一直不显示 3/3类·149词 就是这个原因
        lay.addWidget(self._value)
        self._value.setVisible(bool(value))
        if self._chev is not None:
            lay.addWidget(self._chev)
        for w in (self.icon, self._label, self._value):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if self._chev is not None:
            self._chev.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    # 状态
    def set_value(self, text: str):
        self._value.setText(text)
        self._value.setVisible(bool(text))

    def set_active(self, active: bool):
        active = bool(active)
        if active != self._active:
            self._active = active
            self._sync_colors()
            self.update()

    def is_active(self) -> bool:
        return self._active

    def set_accent(self, on: bool):
        """强调项（新建框选）：文字和图标常亮，底色比普通项重一档。"""
        self._accent = bool(on)
        self._sync_colors()
        self.update()

    def _sync_colors(self):
        if not self.isEnabled():
            self.icon.set_color("#5A5A5A")
            self._label.setStyleSheet(
                f"color:#5A5A5A; font-size:{px(13, self._scale)}px;"
                f" background:transparent;")
            return
        on = self._active or self._accent
        color = "#F4F4F4" if on else TEXT_2
        self.icon.set_color("#F4F4F4" if self._accent else
                            ("#EAEAEA" if self._active else TEXT_2))
        self._label.setStyleSheet(
            f"color:{color}; font-size:{px(13, self._scale)}px;"
            f" font-weight:{600 if on else 400}; background:transparent;")

    def changeEvent(self, event):
        if event.type() == QEvent.EnabledChange:
            self._sync_colors()
            self.update()
        super().changeEvent(event)

    # 交互
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        # 选中底色由侧栏滑块负责，这里再画一层会把滑块盖掉
        if self._active or not (self._hover or self._accent):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(SEL_BG_HOVER))
        r = px(R_SM, self._scale)
        p.drawRoundedRect(QRectF(self.rect()), r, r)
        p.end()


# ---------------------------------------------------------------- 按钮
class AnimatedButton(QPushButton):
    """自绘圆角按钮：悬停 / 按下时底色与文字色做补间，不是硬切。

    QSS 没法写 transition，所以这类"会被反复点"的按钮干脆全部自绘：
    只用一个 0→1 的进度值驱动颜色插值，按下时整体再内缩半个像素、
    文字下沉半像素，手感就出来了。
    """

    _PALETTE = {
        # kind: (底, 悬停底, 按下底, 字, 悬停字, 描边, 悬停描边)
        "primary": ("#EFEFEF", "#FFFFFF", "#D8D8D8", "#121212", "#121212", None, None),
        "chip": ("#242424", "#2E2E2E", "#1F1F1F", "#DBDBDB", "#FFFFFF",
                 "#3B3B3B", "#545454"),
        "chip_primary": ("#EFEFEF", "#FFFFFF", "#D8D8D8", "#121212", "#121212",
                         "#EFEFEF", "#FFFFFF"),
    }

    def __init__(self, text: str, scale: float = 1.0, kind: str = "chip",
                 primary: bool = False, parent=None):
        super().__init__(text, parent)
        self._scale = scale
        self._kind = "chip_primary" if (kind == "chip" and primary) else kind
        self._hover = 0.0
        self._press = 0.0

        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMinimumHeight(px(33 if self._kind != "primary" else 36, scale))

        self._anim_h = QVariantAnimation(self)
        self._anim_h.setDuration(140)
        self._anim_h.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_h.valueChanged.connect(self._on_hover)

        self._anim_p = QVariantAnimation(self)
        self._anim_p.setDuration(90)
        self._anim_p.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_p.valueChanged.connect(self._on_press)

    # -- 动画 --
    def _on_hover(self, value):
        self._hover = float(value)
        self.update()

    def _on_press(self, value):
        self._press = float(value)
        self.update()

    def _run(self, anim, start, end):
        if abs(start - end) < 0.01:
            anim.stop()
            value = end
            if anim is self._anim_h:
                self._hover = value
            else:
                self._press = value
            self.update()
            return
        anim.stop()
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.start()

    def enterEvent(self, event):
        if self.isEnabled():
            self._run(self._anim_h, self._hover, 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._run(self._anim_h, self._hover, 0.0)
        self._run(self._anim_p, self._press, 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._run(self._anim_p, self._press, 1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._run(self._anim_p, self._press, 0.0)
        super().mouseReleaseEvent(event)

    # -- 尺寸 --
    def _font(self) -> QFont:
        f = QFont(self.font())
        if f.pixelSize() <= 0:
            f.setPixelSize(px(13, self._scale))
        f.setWeight(QFont.DemiBold if self._kind != "chip" else QFont.Normal)
        return f

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        pad = px(20 if self._kind == "chip" else 18, self._scale)
        return QSize(fm.horizontalAdvance(self.text()) + pad * 2,
                     self.minimumHeight())

    # -- 绘制 --
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        base, hover_bg, press_bg, fg, hover_fg, bd, hover_bd = \
            self._PALETTE[self._kind]

        inset = float(self._press) * 0.6 * self._scale
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        radius = (rect.height() / 2.0) if self._kind != "primary" \
            else float(px(R_CTRL, self._scale))

        if not self.isEnabled():
            bg = QColor("#1E1E1E")
            fg_color = QColor("#5A5A5A")
            bd_color = QColor("#2A2A2A") if bd else None
        else:
            bg = _mix(_mix(QColor(base), QColor(hover_bg), self._hover),
                      QColor(press_bg), self._press)
            fg_color = _mix(QColor(fg), QColor(hover_fg), self._hover)
            bd_color = _mix(QColor(bd), QColor(hover_bd), self._hover) if bd else None

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, bg)
        if bd_color is not None:
            p.setPen(QPen(bd_color, 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        p.setFont(self._font())
        p.setPen(fg_color)
        text_rect = QRectF(rect)
        text_rect.translate(0, float(self._press) * 0.5 * self._scale)
        p.drawText(text_rect, Qt.AlignCenter, self.text())
        p.end()


def chip_label(text: str, scale: float, primary: bool = False) -> QPushButton:
    """胶囊按钮（首页那一排动作）。返回的仍是 QPushButton，外部用法不变。"""
    btn = AnimatedButton(text, scale, kind="chip", primary=primary)
    btn.setProperty("chip", True)
    if primary:
        btn.setProperty("primary", True)
    return btn


# ---------------------------------------------------------------- 开关
class ToggleSwitch(QAbstractButton):
    """胶囊开关。中性配色：灰底 = 关，白底 + 深色圆点 = 开。"""

    def __init__(self, scale: float = 1.0, parent=None):
        super().__init__(parent)
        self._scale = scale
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setFixedSize(px(42, scale), px(23, scale))

        self._pos = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(self.width(), self.height())

    def _on_anim(self, value):
        self._pos = float(value)
        self.update()

    def _animate(self, checked: bool):
        end = 1.0 if checked else 0.0
        if abs(self._pos - end) < 0.01:
            self._pos = end
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(end)
        self._anim.start()

    def set_checked_silent(self, checked: bool):
        """配置回填：直接落位，不播动画、不发 toggled。"""
        blocked = self.blockSignals(True)
        super().setChecked(bool(checked))
        self.blockSignals(blocked)
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        t = self._pos

        # 轨道颜色跟着补间走：灰 -> 白不是瞬间切换
        track = _mix(QColor("#2E2E2E"), QColor(ACCENT), t)
        if not self.isEnabled():
            track.setAlpha(70)
        elif self.underMouse():
            track = track.lighter(112) if t > 0.5 else QColor("#3A3A3A")
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2.0, h / 2.0)

        pad = px(2.5, self._scale)
        knob = h - pad * 2
        # 过半时稍微"胖"一点，像是被推过去的手感
        bulge = 1.0 + 0.06 * math.sin(math.pi * t)
        x = pad + t * (w - knob - pad * 2)
        knob_color = _mix(QColor("#7C7C7C"), QColor("#171717"), t)
        if not self.isEnabled():
            knob_color = QColor("#565656")
        p.setBrush(knob_color)
        size = knob * bulge
        p.drawEllipse(QRectF(x + (knob - size) / 2, pad + (knob - size) / 2,
                             size, size))
        p.end()


class SwitchRow(QWidget):
    """一整行都是一个开关：文字在左，开关在右，点哪儿都算。"""

    toggled = Signal(bool)

    def __init__(self, label: str, scale: float = 1.0, hint: str = "",
                 parent=None):
        super().__init__(parent)
        self._scale = scale
        self.setObjectName("swrow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self._pressed = False

        lay = QHBoxLayout(self)
        # 左右不留内边距：卡片自己已经有 17px，再留 10px 会和上面的
        # 卡片标题错开一格，看起来怎么都不齐
        lay.setContentsMargins(0, px(8, scale), 0, px(8, scale))
        lay.setSpacing(px(12, scale))

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(px(2, scale))
        self._label = QLabel(label)
        self._label.setObjectName("swlabel")
        text.addWidget(self._label)
        self._hint = None
        if hint:
            self._hint = QLabel(hint)
            self._hint.setObjectName("swhint")
            self._hint.setWordWrap(True)
            text.addWidget(self._hint)
        lay.addLayout(text, 1)

        self.switch = ToggleSwitch(scale)
        lay.addWidget(self.switch, 0, Qt.AlignVCenter)
        self.switch.toggled.connect(self.toggled.emit)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        inside = self.rect().contains(event.position().toPoint())
        if event.button() == Qt.LeftButton and self._pressed and inside:
            self._pressed = False
            self.switch.toggle()
            event.accept()
            return
        self._pressed = False
        super().mouseReleaseEvent(event)

    def setChecked(self, checked: bool):
        self.switch.set_checked_silent(checked)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setToolTip(self, text: str):
        super().setToolTip(text)
        self.switch.setToolTip(text)


# ---------------------------------------------------------------- 分段选择
class SegmentedControl(QWidget):
    """一排互斥按钮，替代下拉框。

    轨道和选中块都是自绘的：选中块是一条会滑动的圆角矩形，
    切选项时用 220ms 的缓动滑过去，比逐个按钮变色顺得多。
    """

    picked = Signal(str)

    def __init__(self, options, current: str, scale: float = 1.0, parent=None):
        super().__init__(parent)
        self._scale = scale
        self._hl = QRectF()
        self.setFixedHeight(px(36, scale))
        # 只靠内容撑宽度的话，两个选项会挤成一小坨；给个下限，轨道才像轨道
        self.setMinimumWidth(px(248, scale))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(3, scale), px(3, scale), px(3, scale), px(3, scale))
        lay.setSpacing(px(2, scale))

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}
        for key, label in options:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("seg", True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setChecked(key == current)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(lambda _c=False, k=key: self.picked.emit(k))
            self._group.addButton(btn)
            lay.addWidget(btn, 1)
            self._buttons[key] = btn

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    # -- 动画 --
    def _on_anim(self, value):
        if isinstance(value, QRectF):
            self._hl = QRectF(value)
            self.update()

    def _target(self) -> QRectF:
        btn = self._buttons.get(self.current())
        if btn is None:
            return QRectF()
        return QRectF(btn.geometry())

    def _sync(self, animate: bool):
        target = self._target()
        if target.isNull() or target.width() <= 0:
            return
        if animate and not self._hl.isNull() and self._hl.width() > 0 \
                and self.isVisible():
            if abs(self._hl.x() - target.x()) < 0.5:
                self._hl = target
                self.update()
                return
            self._anim.stop()
            self._anim.setStartValue(self._hl)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._hl = target
            self.update()

    # -- API --
    def set_value(self, key: str):
        btn = self._buttons.get(str(key))
        if btn is None:
            return
        if not btn.isChecked():
            btn.setChecked(True)
        self._sync(animate=True)

    def current(self) -> str:
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return ""

    # -- 事件 --
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync(animate=False)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync(animate=False)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = float(px(R_CTRL, self._scale))
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        track = QPainterPath()
        track.addRoundedRect(outer, r, r)
        # 轨道要比卡片底色亮一档，否则 #1E1E1E 落在 #1B1B1B 卡片上根本看不见
        p.fillPath(track, QColor("#262626"))
        p.setPen(QPen(QColor("#313131"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(track)

        if not self._hl.isNull() and self._hl.width() > 0:
            hl = self._hl.adjusted(0.5, 0.5, -0.5, -0.5)
            hr = max(2.0, r - px(1, self._scale))
            path = QPainterPath()
            path.addRoundedRect(hl, hr, hr)
            p.fillPath(path, QColor("#4C4C4C"))
            grad = QLinearGradient(0, hl.top(), 0, hl.bottom())
            grad.setColorAt(0.0, QColor(255, 255, 255, 20))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.save()
            p.setClipPath(path)
            p.fillRect(hl, grad)
            p.restore()
            p.setPen(QPen(QColor("#5E5E5E"), 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.end()


# ---------------------------------------------------------------- 角落选择
class CornerPicker(QWidget):
    """在「贴图示意」里直接点四个角，比一排文字按钮直观得多。

    上半部分画一块假的贴图（几条文字占位 + 一条高亮），状态条会出现在
    当前选中的角上；四个角各有一个热区，点谁就挪到谁那儿。
    """

    picked = Signal(str)
    LABELS = {"top_left": "左上", "top_right": "右上",
              "bottom_left": "左下", "bottom_right": "右下"}

    def __init__(self, current: str = "bottom_right", scale: float = 1.0,
                 parent=None):
        super().__init__(parent)
        self._scale = scale
        self._value = current if current in self.LABELS else "bottom_right"
        self._hover = ""
        self.setFixedHeight(px(152, scale))
        self.setMinimumWidth(px(260, scale))
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    # -- API --
    def current(self) -> str:
        return self._value

    def set_value(self, key: str):
        key = str(key)
        if key in self.LABELS and key != self._value:
            self._value = key
            self.update()

    # -- 几何 --
    def _stage(self) -> QRectF:
        m = px(9, self._scale)
        return QRectF(m, m, self.width() - m * 2, self.height() - m * 2)

    def _corner_rects(self):
        s = self._stage()
        w = px(31, self._scale)
        h = px(17, self._scale)
        pad = px(7, self._scale)
        return {
            "top_left": QRectF(s.left() + pad, s.top() + pad, w, h),
            "top_right": QRectF(s.right() - pad - w, s.top() + pad, w, h),
            "bottom_left": QRectF(s.left() + pad, s.bottom() - pad - h, w, h),
            "bottom_right": QRectF(s.right() - pad - w, s.bottom() - pad - h, w, h),
        }

    def _hit(self, pos) -> str:
        for key, rect in self._corner_rects().items():
            if rect.contains(QPointF(pos)):
                return key
        return ""

    # -- 交互 --
    def mouseMoveEvent(self, event):
        key = self._hit(event.position())
        if key != self._hover:
            self._hover = key
            self.setToolTip(
                f"状态条贴到「{self.LABELS[key]}」角" if key else "")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = ""
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            key = self._hit(event.position())
            if key:
                self.set_value(key)
                self.picked.emit(key)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    # -- 绘制 --
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self._stage()
        r = px(10, self._scale)

        path = QPainterPath()
        path.addRoundedRect(s, r, r)
        grad = QLinearGradient(0, s.top(), 0, s.bottom())
        grad.setColorAt(0.0, QColor("#1C1C1C"))
        grad.setColorAt(1.0, QColor("#181818"))
        p.fillPath(path, grad)
        p.setPen(QPen(QColor("#2E2E2E"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # 假文字：画成一段「多行正文」，其中一行的中段被标蓝 —— 这才像
        # 贴图里的整句标注。行数少、行又长的话会读成进度条。
        bar_h = max(3.0, float(px(6, self._scale)))
        left = s.left() + px(20, self._scale)
        width = s.width() - px(40, self._scale)
        # 6 行铺满四角之间的可用高度：只画 5 行且都挤在上半部分的话，
        # 下半截会空出一大块，看着像没画完
        # 上下各有一排角槽（17px 高 + 7px 边距），中间这段才是安全区，
        # 行距落在 0.205~0.775 之间就不会蹭到角槽
        rows = ((0.205, 0.90), (0.319, 0.97), (0.433, 0.86),
                (0.547, 0.94), (0.661, 0.58), (0.775, 0.80))
        for i, (fy, frac) in enumerate(rows):
            y = s.top() + s.height() * fy - bar_h / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2C2C2C"))
            p.drawRoundedRect(QRectF(left, y, width * frac, bar_h),
                              bar_h / 2, bar_h / 2)
            if i == 2:
                # 命中的那半句：从行中段起画一小截蓝，两端不贴行尾
                p.setBrush(QColor(78, 141, 255, 150))
                p.drawRoundedRect(
                    QRectF(left + width * frac * 0.34, y,
                           width * frac * 0.26, bar_h),
                    bar_h / 2, bar_h / 2)

        # 状态条示意
        for key, rect in self._corner_rects().items():
            active = key == self._value
            hover = key == self._hover
            radius = px(6, self._scale)
            if active:
                p.setPen(QPen(QColor("#EDEDED"), max(1.0, 1.2 * self._scale)))
                p.setBrush(QColor("#121212"))
                p.drawRoundedRect(rect, radius, radius)
                # 外圈一圈淡淡的辉光，让"当前角"更跳出来
                p.setPen(QPen(QColor(255, 255, 255, 38),
                              max(1.0, 1.6 * self._scale)))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5),
                                  radius + 1.5, radius + 1.5)
            elif hover:
                p.setPen(QPen(QColor("#5A5A5A"), 1))
                p.setBrush(QColor("#2A2A2A"))
                p.drawRoundedRect(rect, radius, radius)
            else:
                # 未选中的角也要看得出来是「可以点的槽位」
                p.setPen(QPen(QColor("#3C3C3C"), 1))
                p.setBrush(QColor("#232323"))
                p.drawRoundedRect(rect, radius, radius)

            if active:
                # 两个小色块，示意「图例 + 耗时」
                dot = max(2.0, px(3, self._scale))
                x = rect.left() + px(6, self._scale)
                cy = rect.center().y()
                for color in ("#4E8DFF", "#F0A020"):
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(color))
                    p.drawEllipse(QPointF(x, cy), dot, dot)
                    x += dot * 3.2
                p.setBrush(QColor("#4A4A4A"))
                p.drawRoundedRect(
                    QRectF(x, cy - dot * 0.8,
                           max(2.0, rect.right() - px(6, self._scale) - x),
                           dot * 1.6), dot, dot)
            elif not hover:
                # 空位中间放个小点，暗示「这里也可以点」
                dot = max(1.5, px(2, self._scale))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#4E4E4E"))
                p.drawEllipse(rect.center(), dot, dot)
        p.end()


# ---------------------------------------------------------------- 指标块
class Metric(QWidget):
    """大数字 + 小标签，卡片里横排几个用。"""

    def __init__(self, value: str, label: str, scale: float = 1.0,
                 parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(3, scale))
        self._value = QLabel(value)
        self._value.setObjectName("metric")
        self._label = QLabel(label)
        self._label.setObjectName("metriclabel")
        lay.addWidget(self._value)
        lay.addWidget(self._label)

    def set_value(self, text: str):
        self._value.setText(text)


def hline(scale: float, color: str = BD_CARD) -> QFrame:
    line = QFrame()
    line.setFixedHeight(max(1, px(1, scale)))
    line.setStyleSheet(f"background:{color}; border:none;")
    return line


def card(scale: float, spacing: float = 8.0):
    """返回 (卡片 QFrame, 垂直布局)。卡片自绘圆角 + 顶部高光。"""
    frame = Card(scale)
    frame.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(px(17, scale), px(14, scale), px(17, scale),
                              px(14, scale))
    layout.setSpacing(px(spacing, scale))
    return frame, layout


def caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("caption")
    return label


__all__ = [
    "AnimatedButton", "Card", "Chevron", "CornerPicker", "LineIcon", "Metric",
    "NavItem", "SegmentedControl", "SilentGrip", "SwitchRow", "ToggleSwitch",
    "card", "caption", "chip_label", "hline", "px",
]
