# -*- coding: utf-8 -*-
"""无边框「浮窗」外壳（V4.6）：真圆角 + 自绘投影 + 顶边高光。

控制面板和安装程序长着同一张脸，这块底板代码本来在 control_panel 里，
拷第二份的时候就该抽出来了 —— 否则以后调一次投影要改两处，必然不同步。

对外只暴露两样东西：

  - `ShellWindow`：继承它，自动获得整窗透明、圆角底板、投影、描边覆盖层，
    以及「从下方 10px 升起」的入场动画；
  - `SHADOW_PAD`：底板之外给投影留的空白（逻辑像素）。

子类需要自绘什么，就重写 `paint_beyond_body()`（例如面板右下角的缩放斜线）。
"""
from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .coords import ui_zoom
from .ui_theme import BG_BOTTOM, BG_ROOT, BG_TOP, R_WIN

# 底板之外给投影留的空白。四边一样宽：投影各向同性（光源当正上方），
# "浮起来"的感觉交给顶边高光和底板自身的明度渐变去表达。
SHADOW_PAD = 26


def px(value: float, scale: float) -> int:
    return max(0, int(round(float(value) * scale)))


class _Rim(QWidget):
    """整窗最上面的一圈 1px 描边（不吃鼠标事件）。

    为什么不直接在 ShellWindow.paintEvent 里画：内容都是子控件，会盖住父控件
    画的那条线。侧栏左上角有圆角、还自带底色，父控件画的描边会在「侧栏右边
    那条缝」处突然断掉。放到最上层单独画，四条边才连续。

    颜色是自上而下渐隐的白：模拟光源在正上方，顶边像被照亮，底边几乎没有。
    这一点点亮边是「悬浮」和「贴片」的分界 —— 没有它，圆角窗口看着像糊在墙上。
    """

    def __init__(self, parent, scale: float, pad: int, radius: int):
        super().__init__(parent)
        self._scale = scale
        self._pad = pad
        self._radius = radius
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        body = QRectF(self.rect()).adjusted(
            self._pad, self._pad, -self._pad, -self._pad)
        r = self._radius
        # 内缩 0.5px，1px 的笔正好落在底板内侧，不会被底板的抗锯齿吃掉
        path = QPainterPath()
        path.addRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), r - 0.5, r - 0.5)
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0.00, QColor(255, 255, 255, 60))
        grad.setColorAt(0.10, QColor(255, 255, 255, 34))
        grad.setColorAt(0.45, QColor(255, 255, 255, 12))
        grad.setColorAt(1.00, QColor(255, 255, 255, 9))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(grad), 1.0))
        p.drawPath(path)
        p.end()


class ShellWindow(QWidget):
    """无边框浮窗外壳。子类只管往里塞内容，底板不用管。"""

    RADIUS = R_WIN              # 圆角（逻辑像素）
    PAD = SHADOW_PAD            # 投影留白
    BODY_TOP = BG_TOP
    BODY_MID = BG_ROOT
    BODY_BOTTOM = BG_BOTTOM

    def __init__(self, parent=None, flags=Qt.Window | Qt.FramelessWindowHint):
        super().__init__(parent, flags)
        self._scale = ui_zoom()
        self._spad = px(self.PAD, self._scale)
        self._rim = None
        self._home_pos = None
        self._anim_running = False
        self._anim_move = False
        self._enter_done = False

        # 整窗透明：四角才是真的圆角，而不是"深色方块抠了个圆角图形"。
        # 阴影和底板都由 paintEvent 自己画 —— 系统给无边框窗口的投影是矩形的，
        # 摆在圆角底下会露出四个直角。
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

    # ---------- 尺寸 ----------
    def _px(self, value: float) -> int:
        return px(value, self._scale)

    def body_rect(self) -> QRectF:
        """底板矩形 = 整窗去掉四周投影留白。"""
        pad = self._spad
        return QRectF(0, 0, self.width(), self.height()).adjusted(
            pad, pad, -pad, -pad)

    # 兼容旧调用名（control_panel 里原本叫 _body_rect）
    _body_rect = body_rect

    def ensure_rim(self):
        """建描边层并置顶；内容建完、resize 之后才能调。"""
        if self._rim is None:
            self._rim = _Rim(self, self._scale, self._spad, self._px(self.RADIUS))
        self._rim.setGeometry(self.rect())
        self._rim.raise_()
        return self._rim

    # ---------- 绘制 ----------
    def _paint_shadow(self, painter, body: QRectF, radius: float):
        """自绘投影：由外向内一圈圈 1px 圆角描边，越靠外越淡。

        为什么不用 QGraphicsDropShadowEffect：那个效果会把整棵子树渲染进
        一张 pixmap 再模糊，面板里每个按钮的 hover 补间都会触发整窗重录，
        125% 屏上 1400x900 一次要十几毫秒，动画立刻变涩。这里每帧只画
        几十条一像素的圆角线，滚动、切页、悬停都毫无负担。

        两层叠加：外层大范围很淡的一圈是「环境遮挡」，里层 6px 内的
        接触阴影更实一点，窗口才像压住了桌面，而不是飘在半空。
        """
        steps = max(10, self._spad)
        painter.setBrush(Qt.NoBrush)
        # 注：这里只能画「同心、不偏移」的一圈圈描边。曾经试过把整圈往下平移
        # 几像素做"影子往下坠"，结果底部漏出一条比桌面还亮的白边 —— 圈线是
        # 1px 的描边不是实心带，平移之后贴边那一小段没有任何圈线经过。
        for i in range(steps, 0, -1):
            t = i / steps
            # 指数衰减：贴边最实，往外掉得先快后慢，比线性更像真实的光学模糊
            alpha = 40.0 * (1.0 - t) ** 1.5                    # 环境遮挡
            near = 1.0 - min(1.0, i / max(1.0, steps * 0.28))  # 贴边权重
            alpha += 70.0 * near ** 2                          # 接触阴影
            if alpha < 0.8:
                continue
            painter.setPen(QPen(QColor(0, 0, 0, int(min(alpha, 150))), 1.0))
            rr = radius + i
            painter.drawRoundedRect(body.adjusted(-i, -i, i, i), rr, rr)

        # 最外一道硬边：把底板和桌面明确切开，圆角才有"厚度"
        edge = QPainterPath()
        edge.addRoundedRect(body.adjusted(-0.5, -0.5, 0.5, 0.5),
                            radius + 0.5, radius + 0.5)
        painter.setPen(QPen(QColor(0, 0, 0, 92), 1.0))
        painter.drawPath(edge)

    def paint_beyond_body(self, painter, body: QRectF):
        """在底板上继续画东西的钩子（默认什么都不画）。"""

    def paintEvent(self, _event):
        """自绘圆角底板 + 投影。

        用 WA_TranslucentBackground 才能得到真正的圆角，但 Qt 6 在该属性下
        会跳过 QWidget 的 QSS 背景绘制（实测整窗 alpha=0），所以这里手动填色。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        body = self.body_rect()
        radius = float(self._px(self.RADIUS))

        self._paint_shadow(painter, body, radius)

        path = QPainterPath()
        path.addRoundedRect(body, radius, radius)
        # 自上而下极淡的一层明度变化：顶部略亮、底部略暗，
        # 比纯色底板多一点点体积感，又不会和卡片抢
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0.00, QColor(self.BODY_TOP))
        grad.setColorAt(0.35, QColor(self.BODY_MID))
        grad.setColorAt(1.00, QColor(self.BODY_BOTTOM))
        painter.fillPath(path, QBrush(grad))

        self.paint_beyond_body(painter, body)
        painter.end()

    # ---------- 事件 ----------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._rim is not None:
            self._rim.setGeometry(self.rect())
            self._rim.raise_()

    def slide_in(self, distance: int = 10, duration: int = 190):
        """入场：从下方 distance 处升到原位（OutCubic）。

        V4.5 之前用 setWindowOpacity 做整窗淡入，但透明窗口在 Windows 上是
        分层窗口，不吃这个属性（实测设 0.5 时屏幕像素只差 3/255，肉眼看不出来），
        动画等于空转。改成位移入场：不依赖合成器、不重录整棵子树。
        """
        try:
            home = self.pos()
            self._home_pos = home
            lift = self._px(distance)

            def step(value):
                self._anim_move = True
                try:
                    self.move(home.x(),
                              int(round(home.y() + lift * (1.0 - float(value)))))
                finally:
                    self._anim_move = False

            def done():
                self._anim_move = True
                try:
                    self.move(home)
                finally:
                    self._anim_move = False
                self._anim_running = False

            anim = QVariantAnimation(self)
            anim.setDuration(duration)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.valueChanged.connect(step)
            anim.finished.connect(done)
            self._win_anim = anim
            self._anim_running = True
            # 先挪到起点再开播，否则第一帧会停在原位闪一下
            self._anim_move = True
            try:
                self.move(home.x(), home.y() + lift)
            finally:
                self._anim_move = False
            anim.start()
        except Exception:
            pass

    def _fade_in(self):
        """旧名字，control_panel 里还在用。"""
        self.slide_in()

    def moveEvent(self, event):
        """入场动画期间若被外部拖走 / 摆位，立刻认账，别再把人拽回原位。"""
        super().moveEvent(event)
        if self._anim_running and not self._anim_move:
            anim = getattr(self, "_win_anim", None)
            if anim is not None:
                anim.stop()
            self._anim_running = False

    def finish_slide_in(self):
        """收起时调用：动画没播完就把窗口放回原位。"""
        if not self._anim_running:
            return
        anim = getattr(self, "_win_anim", None)
        if anim is not None:
            anim.stop()
        self._anim_running = False
        home = self._home_pos
        if home is not None:
            self._anim_move = True
            try:
                self.move(home)
            finally:
                self._anim_move = False

    def center_on_screen(self):
        screen = self.screen() or self.windowHandle().screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.center().x() - self.width() // 2,
                  geo.center().y() - self.height() // 2)
