# -*- coding: utf-8 -*-
"""截图标注工具栏的 16 个线性图标（纯 QPainter 自绘）。

不用图片资源的原因：这些图标要在 100% / 125% / 150% 屏上缩放、
还要跟着「选中 / 悬停 / 禁用」变色，位图一放大就糊。全部按 24x24 的
虚拟画布画线，调用方给多大就等比放大多少。

顺序与微信 / QQ 截图工具栏一致，按两条灰色分隔线分成三组。
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush

# (id, 显示名, 提示)
TOOLS = (
    ("rect", "矩形", "矩形框：拖动画出方框"),
    ("ellipse", "圆形", "椭圆框：拖动画出圆 / 椭圆"),
    ("sticker", "表情", "表情贴纸：点一下放一个"),
    ("arrow", "箭头", "箭头：从起点拖到终点"),
    ("pen", "画笔", "画笔：按住自由涂鸦"),
    ("mosaic", "马赛克", "马赛克：拖动涂抹打码"),
    ("text", "文字", "文字：点击后输入，可改颜色 / 字号"),
)

ADVANCED = (
    ("number", "序号", "添加序号：逐次点击生成 1、2、3…"),
    ("ocr", "提取文字", "OCR：识别选区文字，可一键复制"),
    ("long", "截长图", "截长图：自动向下滚动并拼接"),
)

ACTIONS = (
    ("undo", "撤销", "撤销上一步（Ctrl+Z）"),
    ("save", "保存", "保存到本地（Ctrl+S）"),
    ("pin", "钉在桌面", "生成置顶贴图，不复制到剪贴板"),
    ("share", "发送", "复制到剪贴板，去聊天窗口粘贴即可"),
    ("cancel", "取消", "放弃这次截图（Esc / 右键）"),
    ("done", "完成", "复制到剪贴板并生成贴图（Enter）"),
)

ALL_IDS = tuple(i for i, _, _ in TOOLS + ADVANCED + ACTIONS)
DRAW_TOOLS = {"rect", "ellipse", "arrow", "pen", "mosaic", "text", "number"}
CLICK_TOOLS = {"text", "number", "sticker"}

ICON_COLOR = QColor("#D6D8DC")
ICON_HOVER = QColor("#FFFFFF")
ICON_ACTIVE = QColor("#FFFFFF")
ICON_CANCEL = QColor("#F2645B")
ICON_DONE = QColor("#4FD07A")
ICON_DISABLED = QColor("#5A5C60")


def _polyline(p, pts):
    p.drawPolyline([QPointF(*q) for q in pts])


def _stroke_icon(p, name):
    """在 24x24 画布上画一个图标；笔由调用方设好。"""
    if name == "rect":
        p.drawRect(QRectF(3.5, 4.5, 17, 15))
    elif name == "ellipse":
        p.drawEllipse(QRectF(3.5, 4.5, 17, 15))
    elif name == "sticker":
        p.drawEllipse(QRectF(3, 3, 18, 18))
        p.save()
        col = p.pen().color()
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(9, 10), 1.2, 1.4)
        p.drawEllipse(QPointF(15, 10), 1.2, 1.4)
        p.restore()
        _polyline(p, [(7.5, 14), (9.5, 16.5), (14.5, 16.5), (16.5, 14)])
    elif name == "arrow":
        p.drawLine(QPointF(4.5, 19.5), QPointF(19, 5))
        _polyline(p, [(12.5, 5), (19, 5), (19, 11.5)])
    elif name == "pen":
        _polyline(p, [(4, 20), (6, 16), (17.5, 4.5), (20, 7),
                      (8.5, 18.5), (4, 20)])
        p.drawLine(QPointF(6, 16), QPointF(8.5, 18.5))
    elif name == "mosaic":
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 2, 2)
        p.save()
        col = p.pen().color()
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for r in range(3):
            for c in range(3):
                if (r + c) % 2 == 0:
                    p.drawRect(QRectF(5.5 + c * 4.5, 5.5 + r * 4.5, 3, 3))
        p.restore()
    elif name == "text":
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 2, 2)
        f = QFont("Segoe UI", 11)
        f.setBold(True)
        p.save()
        p.setFont(f)
        p.drawText(QRectF(3, 3, 18, 18), Qt.AlignCenter, "T")
        p.restore()
    elif name == "number":
        p.drawRoundedRect(QRectF(3.5, 5, 14, 14), 2, 2)
        f = QFont("Segoe UI", 10)
        f.setBold(True)
        p.save()
        col = p.pen().color()
        p.setFont(f)
        p.drawText(QRectF(3.5, 5, 14, 14), Qt.AlignCenter, "1")
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(16, 3, 5, 5), 1, 1)
        p.drawRoundedRect(QRectF(16, 10, 5, 5), 1, 1)
        p.restore()
    elif name == "ocr":
        p.drawRoundedRect(QRectF(2.5, 4, 19, 16), 2, 2)
        f = QFont("Microsoft YaHei UI", 10)
        f.setBold(True)
        p.save()
        p.setFont(f)
        p.drawText(QRectF(2.5, 4, 19, 16), Qt.AlignCenter, "文")
        p.restore()
    elif name == "long":
        pen = QPen(p.pen())
        pen.setStyle(Qt.DashLine)
        pen.setDashPattern([2.5, 2.0])
        p.save()
        p.setPen(pen)
        p.drawRoundedRect(QRectF(4, 3, 16, 18), 2, 2)
        p.restore()
        _polyline(p, [(9, 9), (12, 6), (15, 9)])
        _polyline(p, [(9, 15), (12, 18), (15, 15)])
        p.drawLine(QPointF(12, 6), QPointF(12, 18))
    elif name == "undo":
        from PySide6.QtGui import QPainterPath
        path = QPainterPath(QPointF(19, 17))
        path.quadTo(QPointF(6, 19), QPointF(5, 11))
        path.quadTo(QPointF(4.5, 6), QPointF(9.5, 6))
        p.drawPath(path)
        _polyline(p, [(5.5, 3.5), (9.5, 6), (6.5, 9.5)])
    elif name == "save":
        p.drawLine(QPointF(12, 3), QPointF(12, 14))
        _polyline(p, [(8.5, 10.5), (12, 14), (15.5, 10.5)])
        _polyline(p, [(5, 12), (5, 20), (19, 20), (19, 12)])
    elif name == "pin":
        p.drawEllipse(QRectF(8, 3, 8, 6))
        _polyline(p, [(9, 8.5), (7.5, 12), (16.5, 12), (15, 8.5)])
        p.drawLine(QPointF(12, 12), QPointF(12, 21))
        p.drawLine(QPointF(10, 21), QPointF(14, 21))
    elif name == "share":
        from PySide6.QtGui import QPainterPath
        path = QPainterPath(QPointF(4, 16))
        path.quadTo(QPointF(7, 5), QPointF(19.5, 8))
        p.drawPath(path)
        _polyline(p, [(15.5, 5), (19.5, 8), (16.5, 11.5)])
    elif name == "cancel":
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif name == "done":
        _polyline(p, [(4, 13), (10, 19), (20, 5.5)])


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor,
              active: bool = False, enabled: bool = True):
    """在 rect 内居中画图标；active 时先铺一层选中胶囊。"""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    if active:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

    if not enabled:
        color = ICON_DISABLED
    elif name == "cancel":
        color = ICON_CANCEL
    elif name == "done":
        color = ICON_DONE

    side = min(rect.width(), rect.height()) * 0.88
    scale = side / 24.0
    painter.translate(rect.center())
    painter.scale(scale, scale)
    painter.translate(-12, -12)

    width = 2.0 if name in ("done", "cancel") else 1.65
    pen = QPen(QBrush(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    _stroke_icon(painter, name)
    painter.restore()
