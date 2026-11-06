# -*- coding: utf-8 -*-
"""截图编辑态离屏自检（V4.8）。

跑法：
    D:\\App\\Miniconda\\envs\\pyt311\\python.exe sr_check_editor.py

覆盖：图标 16 枚能画出来 / 工具条分组与避让 / 七种标注 / 撤销 /
     合成输出与 PIL 往返 / 马赛克重建 / 长图拼接 / 动作信号。
全部在 offscreen 平台跑，不需要真的框选屏幕。
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import app.coords as coords
coords._scale = 1.0
coords._zoom = 1.0

from app.capture_icons import ACTIONS, ADVANCED, ALL_IDS, TOOLS, draw_icon
from app.capture_toolbar import CaptureToolbar, OptionsBar, StickerPicker, _Separator
from app.screen_capture import Rect
from app.screenshot_editor import ScreenshotEditor, pixmap_to_pil
from app.scroll_capture import best_shift, stitch

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name
          + ("" if ok else "  <-- " + str(detail)))
    if not ok:
        FAILS.append(name)


def _press(widget, pt, button=Qt.LeftButton):
    widget.mousePressEvent(QMouseEvent(
        QEvent.MouseButtonPress, QPointF(pt), button, button, Qt.NoModifier))


def _move(widget, pt):
    widget.mouseMoveEvent(QMouseEvent(
        QEvent.MouseMove, QPointF(pt), Qt.NoButton, Qt.NoButton, Qt.NoModifier))


def _release(widget, pt, button=Qt.LeftButton):
    widget.mouseReleaseEvent(QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(pt), button, button, Qt.NoModifier))


# ---- 1. 图标 ----
print("\n# 1 图标")
for ident in ALL_IDS:
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    draw_icon(p, ident, QRectF(4, 4, 40, 40), QColor("#FFFFFF"))
    p.end()
    img = pm.toImage()
    nonblank = any(img.pixelColor(x, y).alpha() > 0
                   for y in range(0, 48, 3) for x in range(0, 48, 3))
    check("图标可绘制 " + ident, nonblank)
check("图标共 16 枚", len(ALL_IDS) == 16, len(ALL_IDS))
check("三组数量 7 / 3 / 6", (len(TOOLS), len(ADVANCED), len(ACTIONS)) == (7, 3, 6))

# ---- 2. 工具条 ----
print("\n# 2 工具条")
bar = CaptureToolbar()
check("16 个按钮", len(bar._buttons) == 16, len(bar._buttons))
seps = bar.findChildren(_Separator)
check("两条灰色分隔线", len(seps) == 2, len(seps))
bar.set_tool("pen")
check("选中态同步", bar.active_tool() == "pen" and bar._buttons["pen"].isChecked())
fired = []
bar.actionTriggered.connect(lambda a: fired.append(a))
bar.toolSelected.connect(lambda a: fired.append("tool:" + a))
bar._buttons["undo"].click()
bar._buttons["arrow"].click()
check("动作 / 工具信号", "undo" in fired and "tool:arrow" in fired, fired)
screen = QRect(0, 0, 1600, 900)
below = bar.place_for(QRect(500, 200, 400, 300), screen)
check("工具栏优先在选区下方", below.top() > 500, below)
near_bottom = bar.place_for(QRect(500, 560, 400, 320), screen)
check("下方放不下时翻到上方", near_bottom.bottom() < 560, near_bottom)
left = bar.place_for(QRect(0, 100, 100, 100), screen)
check("左边界不外溢", left.left() >= screen.left(), left)

opt = OptionsBar()
check("选项条颜色 8 个", len(opt._colors) == 8)
check("选项条线宽 3 档", len(opt._widths) == 3)
opt._bump_size(6)
check("字号可调", opt.font_size == 26, opt.font_size)
pk = StickerPicker()
check("表情 20 个", pk.layout().count() == 20, pk.layout().count())

# ---- 3. 编辑器构造 ----
print("\n# 3 编辑器")
geom = QRect(0, 0, 1600, 900)
screens = list(QGuiApplication.screens())
if screens:
    g = screens[0].geometry()
    geom = QRect(g.left(), g.top(), max(640, g.width()), max(480, g.height()))
base = QPixmap(geom.width(), geom.height())
bp = QPainter(base)
for y in range(0, base.height(), 8):
    bp.fillRect(0, y, base.width(), 8,
                QColor(40 + (y // 8) % 80, 70, 120))
bp.fillRect(120, 120, 260, 180, QColor(240, 200, 60))
bp.end()

full = Rect(geom.left(), geom.top(), geom.width(), geom.height())
sel = Rect(geom.left() + 100, geom.top() + 80, 400, 300)
ed = ScreenshotEditor(base, full, sel, {})
check("选区映射", ed._sel.width() == 400 and ed._sel.height() == 300, ed._sel)
check("裁剪框映射", ed._crop.x() == 100 and ed._crop.y() == 80, ed._crop)
check("初始撤销栈为空", not ed._items)

p1 = QPoint(ed._sel.left() + 40, ed._sel.top() + 40)
p2 = QPoint(ed._sel.left() + 180, ed._sel.top() + 150)
mid = QPoint((p1.x() + p2.x()) // 2, (p1.y() + p2.y()) // 2)

for tool in ("rect", "ellipse", "arrow", "pen", "mosaic"):
    ed._set_tool(tool)
    _press(ed, p1)
    _move(ed, mid)
    _move(ed, p2)
    _release(ed, p2)
    check("绘制 " + tool, len(ed._items) >= 1 and
          ed._items[-1]["kind"] == tool,
          [i["kind"] for i in ed._items])

ed._set_tool("number")
_press(ed, p1)
check("序号标注", ed._items[-1]["kind"] == "number" and ed._number == 2)
ed._set_tool("sticker")
_press(ed, p2)
check("表情贴纸", ed._items[-1]["kind"] == "sticker")
ed._set_tool("text")
ed._begin_text(p1)
ed._text_edit.setText("测试文字")
ed._commit_text(p1)
check("文字标注", ed._items[-1]["kind"] == "text" and
      ed._items[-1]["text"] == "测试文字")

before = len(ed._items)
ed._undo()
check("撤销一步", len(ed._items) == before - 1)
# 马赛克在栈里被撤销时要重建图层，不能残留
mosaic_index = next((i for i, a in enumerate(ed._items)
                     if a["kind"] == "mosaic"), None)
check("撤销栈里仍有马赛克", mosaic_index is not None)

out = ed._compose()
check("合成尺寸等于选区", out.width() == sel.w and out.height() == sel.h,
      (out.width(), out.height()))
plain = ed._compose_base()
check("标注确实画进了输出", out.toImage() != plain.toImage())
pil = pixmap_to_pil(out)
check("QPixmap -> PIL 尺寸", pil.size == (sel.w, sel.h), pil.size)
check("QPixmap -> PIL 像素一致",
      pil.getpixel((5, 5)) == (
          out.toImage().pixelColor(5, 5).red(),
          out.toImage().pixelColor(5, 5).green(),
          out.toImage().pixelColor(5, 5).blue()))

# 长图模式：输出直接是长图
import numpy as np
from PIL import Image
ed._long_pm = QPixmap.fromImage(
    QImage(np.zeros((500, 200, 3), dtype=np.uint8).tobytes(),
           200, 500, 600, QImage.Format_RGB888))
long_out = ed._compose()
check("长图输出尺寸", long_out.height() == 500, long_out.height())
ed._long_pm = None

# ---- 4. 长图拼接 ----
print("\n# 4 长图拼接")
tall = np.zeros((900, 120, 3), dtype=np.uint8)
for y in range(900):
    tall[y, :, :] = (y % 255, (y * 3) % 255, (y * 7) % 255)
f1 = tall[0:300].copy()
f2 = tall[150:450].copy()
dy = best_shift(f1, f2)
check("能算出滚动位移", dy is not None and abs(dy - 150) <= 4, dy)
st = stitch([f1, f2])
check("拼接高度 = 300 + 位移", abs(st.shape[0] - (300 + dy)) <= 4, st.shape)
check("拼接内容对得上", np.abs(
    st[-dy:].astype(int) - f2[-dy:].astype(int)).mean() < 1.0)

# ---- 5. 收尾 ----
print("\n# 5 结果")
ed._finish("done", out)
check("finish 返回 done", ed.result()["action"] == "done")
check("finish 带回图像", ed.result()["pixmap"] is out)
ed.cleanup()
app.processEvents()
check("cleanup 释放附属窗口", ed._toolbar is None and ed._options is None)

print("\n" + ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS)))
sys.exit(1 if FAILS else 0)
