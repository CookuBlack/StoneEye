# -*- coding: utf-8 -*-
"""从应用图标里取品牌主色，并给所有「框」提供同一套描边画法。

边框颜色不再写死成青色：默认从 `assets/app.png` 统计那些高饱和、
偏蓝且明亮的发光像素，取平均色。换图标时框选线、编辑态选区、贴图外框
会一起跟着变，托盘 / exe / 侧栏和截图框终于是一个色系。

统计只用 96×96 缩略图 + 纯 Python 循环，不依赖 numpy，一次约几毫秒，
结果缓存起来；图标缺失或没取到足够样本时回退到 FALLBACK_HEX。
"""
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from .assets import resource_path

# 由 assets/app.png 亮部统计得到（RGB ≈ 106, 204, 249）
FALLBACK_HEX = "#6ACCF8"


@lru_cache(maxsize=1)
def brand_hex() -> str:
    """图标主色的 #RRGGBB 字符串；读不到图标就用回退色。"""
    try:
        from PIL import Image

        path = resource_path("assets", "app.png")
        im = Image.open(path).convert("RGB").resize((96, 96))
        total = [0, 0, 0]
        count = 0
        for r, g, b in im.getdata():
            hi, lo = max(r, g, b), min(r, g, b)
            if hi < 90 or (hi - lo) / hi < 0.35:
                continue
            if not (b > r + 25 and b > g + 5):   # 只挑蓝色系
                continue
            if (r + g + b) / 3 < 150:            # 只挑发光亮部，别被暗底拉灰
                continue
            total[0] += r
            total[1] += g
            total[2] += b
            count += 1
        if count >= 30:
            return "#%02X%02X%02X" % tuple(
                min(255, round(v / count)) for v in total)
    except Exception:
        pass
    return FALLBACK_HEX


@lru_cache(maxsize=1)
def brand_qcolor() -> QColor:
    return QColor(brand_hex())


def draw_selection_frame(painter: QPainter, rect, color=None,
                         width: float = 3.0, glow: bool = True,
                         handles: bool = True):
    """画一圈统一的选区框：深色衬线 → 外发光 → 主色线 → 四角把手。

    - 深色衬线保证在白底截图上也看得清（图标主色偏亮）；
    - 外发光让框「浮」在遮罩上，而不是硬贴一条线；
    - 四角 L 形把手是微信 / QQ 截图那种可编辑的暗示，也更好看。
    """
    col = QColor(color) if color is not None else brand_qcolor()
    r = QRectF(rect)
    if r.width() < 2 or r.height() < 2:
        return
    painter.save()
    painter.setBrush(Qt.NoBrush)

    if glow:
        painter.setPen(QPen(QColor(6, 12, 20, 150), width + 3.0))
        painter.drawRect(r)
        for i, alpha in ((3, 26), (2, 46), (1, 72)):
            c = QColor(col)
            c.setAlpha(alpha)
            painter.setPen(QPen(c, 1.0))
            painter.drawRect(r.adjusted(-i - 0.5, -i - 0.5, i + 0.5, i + 0.5))

    main = QColor(col)
    main.setAlpha(245)
    painter.setPen(QPen(main, width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
    painter.drawRect(r.adjusted(width / 2.0, width / 2.0,
                                -width / 2.0, -width / 2.0))

    if handles and r.width() > 44 and r.height() > 44:
        length = min(15.0, r.width() / 4.0, r.height() / 4.0)
        hi = QColor(255, 255, 255, 225)
        painter.setPen(QPen(hi, max(2.0, width - 0.4),
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        l, t, rr, bb = r.left(), r.top(), r.right(), r.bottom()
        corners = (
            [(l, t + length), (l, t), (l + length, t)],
            [(rr - length, t), (rr, t), (rr, t + length)],
            [(rr, bb - length), (rr, bb), (rr - length, bb)],
            [(l + length, bb), (l, bb), (l, bb - length)],
        )
        for pts in corners:
            painter.drawPolyline([QPointF(x, y) for x, y in pts])
    painter.restore()
