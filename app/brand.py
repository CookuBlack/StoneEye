# -*- coding: utf-8 -*-
"""从应用图标里取品牌主色，并给所有「框」提供同一套描边画法。

边框颜色不再写死成青色：默认从 `assets/app.png` 统计那些高饱和、
偏蓝且明亮的发光像素，取平均色。换图标时框选线、编辑态选区、贴图外框
会一起跟着变，托盘 / exe / 侧栏和截图框终于是一个色系。

统计只用 96×96 缩略图 + 纯 Python 循环，不依赖 numpy，一次约几毫秒，
结果缓存起来；图标缺失或没取到足够样本时回退到 FALLBACK_HEX。

V4.9.3：框不再只是「画出来好看」。selection_grips / hit_selection /
resize_selection / clamp_selection 把 8 个手柄的几何、命中、拖动计算集中
在这里，框选层和截图编辑态共用同一套坐标语义，改大小时才不会两处错位。
"""
from functools import lru_cache

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from .assets import resource_path

# 由 assets/app.png 亮部统计得到（RGB ≈ 106, 204, 249）
FALLBACK_HEX = "#6ACCF8"

# --- 可调整选区的几何常量（逻辑像素；调用方按 ui_zoom() 缩放）-----------
GRIP_HIT = 11.0          # 手柄命中半径，比看起来大一圈，好抓
EDGE_BAND = 7.0          # 边框线两侧可整体拖动的带宽
MIN_SELECTION = 48.0     # 拖动后的最小选区边长


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


def selection_grips(rect):
    """8 个手柄中心点：四角 + 四边中点。

    键名里的方向字母同时被 resize_selection 当作位标记用：
    "nw" 同时含 n / w，拖它就会改上边和左边。
    """
    r = QRectF(rect)
    cx = (r.left() + r.right()) / 2.0
    cy = (r.top() + r.bottom()) / 2.0
    return {
        "nw": QPointF(r.left(), r.top()),
        "n":  QPointF(cx, r.top()),
        "ne": QPointF(r.right(), r.top()),
        "e":  QPointF(r.right(), cy),
        "se": QPointF(r.right(), r.bottom()),
        "s":  QPointF(cx, r.bottom()),
        "sw": QPointF(r.left(), r.bottom()),
        "w":  QPointF(r.left(), cy),
    }


def hit_selection(point, rect, radius: float = GRIP_HIT,
                  band: float = EDGE_BAND):
    """命中测试 → ("resize", 手柄名) / ("move", "") / (None, "")。

    手柄优先于边框带：鼠标落在角上时同时贴着两条边，用户的意图显然是
    改大小而不是把整个框拖走。边框外侧留 band 像素也能抓到，因为框线
    本身只有 3px，贴着线找太难了。
    """
    q = QPointF(point)
    for name, c in selection_grips(rect).items():
        if abs(q.x() - c.x()) <= radius and abs(q.y() - c.y()) <= radius:
            return "resize", name
    r = QRectF(rect)
    outer = r.adjusted(-band, -band, band, band)
    inner = r.adjusted(band, band, -band, -band)
    if not outer.contains(q):
        return None, ""
    if inner.width() <= 0 or inner.height() <= 0 or not inner.contains(q):
        return "move", ""
    return None, ""


def clamp_selection(rect, bounds, min_size: float = MIN_SELECTION) -> QRect:
    """把选区整体关在 bounds 内（尺寸不变，只挪位置）。"""
    if bounds is None:
        return QRect(rect)
    b = QRectF(bounds)
    bl, bt = b.left(), b.top()
    br, bb = bl + b.width(), bt + b.height()
    w = min(max(min_size, float(rect.width())), br - bl)
    h = min(max(min_size, float(rect.height())), bb - bt)
    x = min(max(float(rect.left()), bl), br - w)
    y = min(max(float(rect.top()), bt), bb - h)
    return QRect(round(x), round(y), round(w), round(h))


def resize_selection(rect, handle: str, delta, bounds=None,
                     min_size: float = MIN_SELECTION) -> QRect:
    """按手柄方向推开 1~2 条边，返回新选区。

    rect 用 QRect(x, y, w, h) 的半开语义：x2 = x + w。原代码刻意不用
    QRect(QPoint, QPoint)，就是为了避免右下角多算 1px；这里保持一致。
    """
    r = QRectF(rect)
    x1, y1 = r.left(), r.top()
    x2, y2 = x1 + r.width(), y1 + r.height()
    d = QPointF(delta)
    if "w" in handle:
        x1 = min(x1 + d.x(), x2 - min_size)
    if "e" in handle:
        x2 = max(x2 + d.x(), x1 + min_size)
    if "n" in handle:
        y1 = min(y1 + d.y(), y2 - min_size)
    if "s" in handle:
        y2 = max(y2 + d.y(), y1 + min_size)
    return clamp_selection(
        QRect(round(x1), round(y1), round(x2 - x1), round(y2 - y1)),
        bounds, min_size)


def draw_selection_grips(painter: QPainter, rect, color=None,
                         active=None, scale: float = 1.0):
    """画 8 个白底品牌色描边的小手柄；浅色照片上靠深色外衬保证可见。"""
    col = QColor(color) if color is not None else brand_qcolor()
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    for name, c in selection_grips(rect).items():
        corner = len(name) == 2
        d = (9.0 if corner else 7.0) * scale
        box = QRectF(c.x() - d / 2, c.y() - d / 2, d, d)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(8, 14, 22, 190))
        painter.drawRoundedRect(
            box.adjusted(-1.3, -1.3, 1.3, 1.3), 3.2 * scale, 3.2 * scale)
        on = active == name
        painter.setBrush(col if on else QColor(250, 252, 255))
        painter.setPen(QPen(QColor(255, 255, 255) if on else col,
                            1.2 * scale))
        painter.drawRoundedRect(box, 2.4 * scale, 2.4 * scale)
    painter.restore()


def draw_selection_frame(painter: QPainter, rect, color=None,
                         width: float = 3.0, glow: bool = True,
                         handles: bool = True, grips: bool = False,
                         active_grip=None, grip_scale: float = 1.0):
    """画一圈统一的选区框：深色衬线 → 外发光 → 主色线 → 把手。

    - 深色衬线保证在白底截图上也看得清（图标主色偏亮）；
    - 外发光让框「浮」在遮罩上，而不是硬贴一条线；
    - handles=True 是旧的四角 L 形装饰；grips=True 换成 8 个可拖动
      手柄（微信 / QQ 截图那种「这个框还能调」的暗示），两者不叠加。
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

    if grips and r.width() > 44 and r.height() > 44:
        draw_selection_grips(painter, r, col, active_grip, grip_scale)
    elif handles and r.width() > 44 and r.height() > 44:
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
