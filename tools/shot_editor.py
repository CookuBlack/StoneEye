# -*- coding: utf-8 -*-
"""离屏渲染截图编辑态预览（开发用）。

    python tools/shot_editor.py

产出 docs/preview/toolbar.png（16 键工具条 + 选项条 + 表情板）
和 docs/preview/editor.png（带各种标注的编辑态）。
本机会话抓屏全黑，所以内容用 QPainter 造一张假的桌面窗口。
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QMouseEvent,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
for _f in (r"C:/Windows/Fonts/msyh.ttc", r"C:/Windows/Fonts/seguiemj.ttf",
           r"C:/Windows/Fonts/segoeui.ttf"):
    if os.path.exists(_f):
        QFontDatabase.addApplicationFont(_f)
app.setFont(QFont("Microsoft YaHei UI", 9))
import app.coords as coords
coords._scale = 1.0
coords._zoom = 1.0

from app.capture_toolbar import CaptureToolbar, OptionsBar, StickerPicker
from app.screen_capture import Rect
from app.screenshot_editor import ScreenshotEditor

W, H = 1200, 760
OUT = os.path.join(ROOT, "docs", "preview")
os.makedirs(OUT, exist_ok=True)


def pump(ms=180):
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def fake_desktop():
    pm = QPixmap(W, H)
    pm.fill(QColor(238, 240, 244))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(QRectF(90, 60, 1020, 640), 14, 14)
    p.setBrush(QColor(30, 34, 42))
    p.drawRoundedRect(QRectF(90, 60, 1020, 46), 14, 14)
    p.fillRect(90, 90, 1020, 16, QColor(30, 34, 42))
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(17)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(240, 240, 240))
    p.drawText(120, 90, "分镜脚本 · 第三章")
    f.setPixelSize(21)
    p.setFont(f)
    lines = [
        ("镜头缓慢向前推进，全景。随后切到大特写。", QColor(40, 42, 48)),
        ("她抬起头，视线越过窗框落到远处的山脊上。", QColor(60, 62, 68)),
        ("中景，摇镜跟随；切到近景，手持轻微晃动。", QColor(40, 42, 48)),
        ("画面定格，字幕浮出：三天之后。", QColor(60, 62, 68)),
    ]
    y = 175
    for text, col in lines:
        p.setPen(col)
        p.drawText(140, y, text)
        p.setPen(QColor(225, 225, 228))
        p.drawLine(140, y + 18, 1040, y + 18)
        y += 78
    f.setPixelSize(15)
    f.setBold(False)
    p.setFont(f)
    p.setPen(QColor(120, 124, 132))
    p.drawText(140, 560, "选中区域后可标注：矩形 / 圆形 / 箭头 / 画笔 / 马赛克 / 文字 / 序号")
    p.setBrush(QColor(78, 141, 255))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(140, 600, 150, 42), 8, 8)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(140, 600, 150, 42), Qt.AlignCenter, "开始识别")
    p.end()
    return pm


def press(ed, pt):
    ed.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(pt),
                                   Qt.LeftButton, Qt.LeftButton,
                                   Qt.NoModifier))


def move(ed, pt):
    ed.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(pt), Qt.NoButton,
                                  Qt.NoButton, Qt.NoModifier))


def release(ed, pt):
    ed.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(pt),
                                     Qt.LeftButton, Qt.LeftButton,
                                     Qt.NoModifier))


def composite(widget, out, bg=(236, 238, 242)):
    pm = widget.grab()
    canvas = QPixmap(pm.size())
    canvas.fill(QColor(*bg))
    p = QPainter(canvas)
    p.drawPixmap(0, 0, pm)
    p.end()
    canvas.save(out)
    print("saved", out, canvas.width(), canvas.height())


def composite_on(widget, out, background):
    """把透明遮罩层叠到一张真实底图上（框选预览用）。

    QWidget.grab() 会把 CompositionMode_Clear 挖出来的洞填成不透明白，
    真机上那块是透明、直接透出桌面的；预览里要手动把洞再抠透明一次，
    否则看起来像选区内容被一块白板盖住了。
    """
    pm = widget.grab()
    active = getattr(widget, "_active_rect", lambda: None)()
    if active is not None:
        r = active.translated(-widget._offset.x(), -widget._offset.y())
        m = QPainter(pm)
        m.setCompositionMode(QPainter.CompositionMode_Clear)
        # 只抠内部：外沿 6px 留着，那是画在挖空之后的边框 / 发光 / 把手
        m.fillRect(r.adjusted(6, 6, -6, -6), QColor(0, 0, 0, 0))
        m.end()
    canvas = QPixmap(pm.size())
    canvas.fill(QColor(236, 238, 242))
    p = QPainter(canvas)
    p.drawPixmap(0, 0, background)
    p.drawPixmap(0, 0, pm)
    p.end()
    canvas.save(out)
    print("saved", out, canvas.width(), canvas.height())


def composite_with_children(ed, out, bg=(236, 238, 242)):
    """编辑态全屏窗口 + 浮在它上面的工具条 / 选项条，合成一张。"""
    canvas = QPixmap(ed.size())
    canvas.fill(QColor(*bg))
    p = QPainter(canvas)
    p.drawPixmap(0, 0, ed.grab())
    for w in (ed._options, ed._toolbar):
        if w is not None and w.isVisible():
            p.drawPixmap(w.pos(), w.grab())
    p.end()
    canvas.save(out)
    print("saved", out, canvas.width(), canvas.height())


def main():
    base = fake_desktop()
    full = Rect(0, 0, W, H)
    sel = Rect(110, 120, 980, 480)

    ed = ScreenshotEditor(base, full, sel, {})
    ed.setGeometry(0, 0, W, H)
    ed._geom = QRect(0, 0, W, H)
    ed._sel = QRect(110, 120, 980, 480)
    ed.show()
    ed._place_children()
    ed._toolbar.show()
    ed._set_tool("rect")
    ed._options.show()
    pump()

    # 造一批标注，展示七种工具的效果
    ed._set_color("#F2645B")
    ed._set_width(4)
    ed._set_tool("rect")
    press(ed, QPoint(150, 155)); move(ed, QPoint(520, 220))
    release(ed, QPoint(520, 220))
    ed._set_tool("ellipse")
    press(ed, QPoint(600, 300)); move(ed, QPoint(820, 420))
    release(ed, QPoint(820, 420))
    ed._set_color("#4E8DFF")
    ed._set_tool("arrow")
    press(ed, QPoint(200, 400)); move(ed, QPoint(420, 330))
    release(ed, QPoint(420, 330))
    ed._set_color("#4FD07A")
    ed._set_tool("pen")
    press(ed, QPoint(700, 180))
    for i in range(12):
        move(ed, QPoint(700 + i * 12, 180 + int(26 * __import__("math").sin(i / 2.0))))
    release(ed, QPoint(832, 180))
    ed._set_tool("mosaic")
    press(ed, QPoint(150, 325))
    for i in range(14):
        move(ed, QPoint(150 + i * 18, 325))
    release(ed, QPoint(384, 325))
    ed._set_tool("number")
    press(ed, QPoint(560, 165))
    press(ed, QPoint(900, 470))
    ed._set_tool("sticker")
    press(ed, QPoint(950, 200))
    ed._set_tool("text")
    ed._begin_text(QPoint(240, 290))
    ed._text_edit.setText("这里要改为近景")
    ed._commit_text(QPoint(240, 290))
    ed._set_tool("rect")
    ed.update()
    pump(250)

    composite(ed._toolbar, os.path.join(OUT, "toolbar.png"), (222, 224, 228))
    composite(ed._options, os.path.join(OUT, "toolbar_options.png"), (222, 224, 228))
    ed._picker.show(); pump(100)
    composite(ed._picker, os.path.join(OUT, "stickers.png"), (222, 224, 228))
    ed._picker.hide()
    composite(ed, os.path.join(OUT, "editor.png"), (236, 238, 242))
    ed._compose().save(os.path.join(OUT, "editor_result.png"), "PNG")
    print("saved", os.path.join(OUT, "editor_result.png"))
    ed._hide_children()
    ed.hide()

    # 框选层：模拟一次拖拽，叠到假桌面上看新的「图标主色 + 加粗 + 四角把手」
    from app.region_selector import RegionSelector
    rs = RegionSelector()
    rs.setGeometry(0, 0, W, H)
    rs._offset = QPoint(0, 0)
    rs._origin = QPoint(150, 140)
    rs._current = QPoint(1050, 580)
    rs._dragging = True
    rs.show()
    pump(120)
    composite_on(rs, os.path.join(OUT, "selection.png"), base)
    rs.hide()

    # 二次编辑：拿一块「已有红色标注」的成品图，在深色编辑台上继续画绿框
    prev = base.copy(QRect(110, 120, 700, 380))
    pp = QPainter(prev)
    pp.setPen(QPen(QColor("#F2645B"), 4))
    pp.drawRect(QRectF(30, 30, 240, 70))
    pp.end()
    re = ScreenshotEditor.for_image(prev, {})
    re.show()
    re._place_children()
    re._show_child(re._toolbar)
    re._set_tool("rect")
    ctr = re._sel.center()
    re._items.append(dict(
        kind="rect", p1=QPoint(ctr.x() - 130, ctr.y() - 55),
        p2=QPoint(ctr.x() + 130, ctr.y() + 55),
        color="#4FD07A", width=4))
    re.update()
    pump(220)
    composite_with_children(re, os.path.join(OUT, "editor_reedit.png"))
    re._hide_children()
    re.hide()
    re.cleanup()


if __name__ == "__main__":
    main()
