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
check("默认线宽落在档位里",
      opt.pen_width in tuple(w for w, _ in opt._widths), opt.pen_width)
check("默认线宽有选中态",
      any(b.isChecked() for w, b in opt._widths if w == opt.pen_width))
opt.set_mode("text")
check("文字模式隐藏线宽组并保留字号分隔线",
      all(b.isHidden() for _, b in opt._widths)
      and not opt._size_sep.isHidden())
opt.set_mode("rect")
check("绘制模式不留孤立分隔线", opt._size_sep.isHidden()
      and all(not b.isHidden() for _, b in opt._widths))
opt._bump_size(6)
check("字号可调", opt.font_size == 26, opt.font_size)
pk = StickerPicker()
check("表情 20 个", pk.layout().count() == 20, pk.layout().count())

# ---- 2.5 动作按钮归类（V4.9.1）----
print("\n# 2.5 动作按钮归类")
for ident in ("ocr", "long"):
    check("%s 不应该是复选工具" % ident,
          not bar._buttons[ident].isCheckable())
    fired.clear()
    bar._buttons[ident].click()
    check("点击 %s 发出动作信号" % ident, fired == [ident], fired)
for ident in ("rect", "number"):
    check("%s 仍是复选工具" % ident, bar._buttons[ident].isCheckable())
check("动作集合恰好是 8 个（6 个常规 + ocr + long）",
      len(__import__("app.capture_toolbar", fromlist=["ACTION_IDS"]).ACTION_IDS) == 8)

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

# ---- 3.5 序号撤销重编号 / OCR / 长图动作链路 ----
print("\n# 3.5 序号重编号与动作链路")
ne = ScreenshotEditor(base, full, sel, {})
ne._set_tool("number")
seq = [QPoint(ed._sel.left() + 20 + i * 26, ed._sel.top() + 30)
       for i in range(6)]
for pt in seq:
    _press(ne, pt)
check("连续标注 1..6", [a["n"] for a in ne._items] == [1, 2, 3, 4, 5, 6],
      [a["n"] for a in ne._items])
for _ in range(3):
    ne._undo()
check("撤销 3 个后画面编号仍连续",
      [a["n"] for a in ne._items] == [1, 2, 3], [a["n"] for a in ne._items])
check("撤销后下一个序号是 4", ne._number == 4, ne._number)
_press(ne, seq[3])
check("接着标从 4 开始", ne._items[-1]["n"] == 4)
for _ in range(4):
    ne._undo()
check("全部撤销后计数归 1", ne._number == 1 and not ne._items,
      (ne._number, len(ne._items)))
_press(ne, seq[0])
check("重新标注从 1 开始", ne._items[-1]["n"] == 1, ne._items[-1]["n"])
ne._set_tool("rect")
_press(ne, seq[1]); _move(ne, seq[2]); _release(ne, seq[2])
ne._undo()
check("撤销非序号标注不打乱编号",
      ne._number == 2 and ne._items[-1]["n"] == 1)

calls = []
ne._run_ocr = lambda: calls.append("ocr")
ne._run_long = lambda: calls.append("long")
ne._on_action("ocr")
ne._on_action("long")
check("编辑器接住 ocr / long 动作", calls == ["ocr", "long"], calls)
ne._busy = True
ne._on_action("save")
check("busy 时其它动作被拦下", calls == ["ocr", "long"], calls)
ne._on_action("cancel")
check("busy 时取消仍可用", ne.result()["action"] == "cancel")
ne._busy = True
ne._toolbar.set_busy(True)
ne._ocr_thread_done()
check("线程收工兜底解锁工具条",
      ne._busy is False and ne._toolbar._buttons["ocr"].isEnabled())

from app.screenshot_editor import OcrTextPanel
panel = OcrTextPanel("第一行\n第二行")
check("OCR 面板初始文本", panel._box.toPlainText() == "第一行\n第二行")
panel.set_text("新结果")
check("OCR 面板可复用换文本",
      panel._box.toPlainText() == "新结果" and panel._text == "新结果")
panel._copy_all()
check("复制按钮给出反馈", "已复制" in panel._btn_copy.text())
panel.hide(); panel.deleteLater()
ne.cleanup()
app.processEvents()

re2 = ScreenshotEditor.for_image(base, {})
re2._run_long()
check("二次编辑拒绝截长图（没有可滚动的原窗口）",
      re2._long_pm is None and re2._busy is False)
re2.cleanup()
app.processEvents()

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

# ---- 4.5 品牌色 / 上浮 / 二次编辑 ----
print("\n# 4.5 品牌色 / 上浮 / 二次编辑")
from app.brand import FALLBACK_HEX, brand_hex, brand_qcolor, draw_selection_frame
from app.config_manager import ConfigManager
from app.window_manager import snapshot_position

hexv = brand_hex()
check("图标主色是合法 #RRGGBB",
      len(hexv) == 7 and hexv.startswith("#"), hexv)
c = brand_qcolor()
check("主色取自图标蓝色亮部",
      c.isValid() and c.blue() > c.red() + 40,
      (c.red(), c.green(), c.blue()))
check("图标缺失时回退色可用", QColor(FALLBACK_HEX).isValid())

frame_pm = QPixmap(120, 80)
frame_pm.fill(QColor(255, 255, 255))
fp = QPainter(frame_pm)
draw_selection_frame(fp, QRectF(10, 10, 100, 60), width=3.0)
fp.end()
fimg = frame_pm.toImage()
hits = sum(1 for y in range(0, 80, 2) for x in range(0, 120, 2)
           if (lambda q: q.blue() > 150 and q.blue() > q.red() + 40)(
               fimg.pixelColor(x, y)))
check("选区框画上主色描边 + 四角把手", hits > 30, hits)

defaults = ConfigManager.defaults()["sticker"]
check("边框色默认跟随图标", defaults["border_color"] == "")
check("上浮距离默认 14", defaults["float_lift"] == 14)
check("贴图上浮 14px",
      snapshot_position(Rect(300, 300, 200, 100), 14, 1.0) == (300, 286))
check("lift=0 时仍完全原位",
      snapshot_position(Rect(300, 300, 200, 100), 0, 1.0) == (300, 300))

base = QPixmap(400, 240)
base.fill(QColor(250, 250, 250))
bp = QPainter(base)
bp.setBrush(QColor(220, 30, 30))
bp.drawRect(0, 0, 40, 40)
bp.end()
re = ScreenshotEditor.for_image(base, {})
check("for_image 标记为二次编辑", re._is_reedit is True)
check("for_image 记录原始分辨率", re._export_size == (400, 240))
check("选区与裁剪矩形对齐",
      re._crop.x() == re._sel.x() and re._crop.width() == re._sel.width())
ctr = re._sel.center()
re._items.append(dict(
    kind="rect", p1=QPoint(ctr.x() - 30, ctr.y() - 20),
    p2=QPoint(ctr.x() + 30, ctr.y() + 20),
    color="#4E8DFF", width=4))
reout = re._compose()
check("二次编辑输出保持原分辨率",
      (reout.width(), reout.height()) == (400, 240))
rimg = reout.toImage()
check("已有标注仍在原图上",
      rimg.pixelColor(5, 5).red() > 180 and rimg.pixelColor(5, 5).green() < 80)
check("底图白底未被染色", rimg.pixelColor(200, 10).red() > 240)
check("新标注落在正确位置", any(
    rimg.pixelColor(x, 120).blue() > rimg.pixelColor(x, 120).red() + 30
    for x in range(120, 290)))
re.cleanup()
app.processEvents()

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
