# -*- coding: utf-8 -*-
"""控制面板离屏校验（V4.3 · 五页两栏版）。

覆盖：
  1. 构造与配置回填不产生任何写回（否则一打开面板就把配置改了）
  2. 开关整行点击 / 分段选择 / 滑块拖动 → 推送内容与数值换算正确
  3. 「恢复默认」只推一次合并补丁
  4. 侧栏分页切换（导航高亮 + 版式高度）
  5. 角落示意图点选（点哪个角就贴哪个角）
  6. 识别规则页：点侧栏是翻页而不是另开窗口 / 改动标记 / 放弃修改 / 保存推送
  7. 隐藏时记住窗口几何，再次打开还原到原位置
  8. 快捷键与 Esc 收起
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

app = QApplication.instance() or QApplication(sys.argv)
for _font in (r"C:/Windows/Fonts/msyh.ttc",):
    if os.path.exists(_font):
        QFontDatabase.addApplicationFont(_font)
app.setFont(QFont("Microsoft YaHei UI", 9))

import app.coords as coords
coords._scale = 1.0          # 离屏平台的物理/逻辑比不可信，固定 100%
coords._zoom = 1.0           # 界面放大倍数同样固定，否则结果随本机 DPI 变

from app.config_manager import ConfigManager
from app.control_panel import ControlPanel

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  <-- " + str(detail)))
    if not ok:
        FAILS.append(name)


class FakeService:
    def stats(self):
        return dict(ready=True, attempted=True, last_ms=420.0, avg_ms=480.0,
                    queue=0, ocr_count=7)


class FakeManager:
    def __init__(self):
        self._n = 2
    def count(self):
        return self._n
    def latest_text(self):
        return "镜头缓慢向前推进，全景。"


class FakeController:
    """只记录补丁，不碰真实服务。"""

    def __init__(self):
        self.config_mgr = ConfigManager(r"D:/ProgramProject/Python/ScreenshotRecognition/config.json")
        self.cfg = self.config_mgr.load()
        self.service = FakeService()
        self.manager = FakeManager()
        self.patches = []
        self.calls = []

    def apply_panel_patch(self, patch, reocr=False):
        self.patches.append((patch, reocr))
        for section, values in patch.items():
            target = self.cfg.setdefault(section, {})
            if isinstance(values, dict) and isinstance(target, dict):
                target.update(values)
        return True

    def save_config_now(self): self.calls.append("save")
    def reload_config(self): self.calls.append("reload")
    def open_config(self): self.calls.append("open_config")
    def start_selection(self): self.calls.append("select")
    def recognize_all(self): self.calls.append("reocr")
    def close_all(self): self.calls.append("close_all")


def click_row(row):
    """模拟整行点击（按下 + 松开）。"""
    QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier,
                     QPoint(row.width() // 2, row.height() // 2))


ctl = FakeController()
panel = ControlPanel(ctl)
panel.move(30, 30)
panel.show()
for _ in range(5):
    app.processEvents()

# ---- 1. 打开面板不应该写回配置 ----
check("构造 + 回填不产生写回", len(ctl.patches) == 0, ctl.patches)
check("开关状态与配置一致",
      panel._sw_guard.isChecked() is True and panel._sw_snap.isChecked() is True)

# ---- 2. 开关整行点击 ----
ctl.patches.clear()
before = panel._sw_guard.isChecked()
click_row(panel._sw_guard)
app.processEvents()
check("点击语义校验整行 -> 反相",
      panel._sw_guard.isChecked() is (not before), panel._sw_guard.isChecked())
check("推送 live.context_guard 且要求重识别",
      ctl.patches and ctl.patches[-1][0] == {"live": {"context_guard": not before}}
      and ctl.patches[-1][1] is True, ctl.patches)
click_row(panel._sw_guard)
app.processEvents()

# ---- 3. 分段选择 ----
ctl.patches.clear()
panel._mode.picked.emit("live")
app.processEvents()
check("模式切到实时刷新", ctl.patches and
      ctl.patches[-1][0] == {"live": {"capture_mode": "live"}}, ctl.patches)
ctl.patches.clear()
panel._grain.picked.emit("word")
app.processEvents()
check("标注粒度切词级（触发重识别）", ctl.patches and
      ctl.patches[-1][0] == {"live": {"highlight_mode": "word"}} and
      ctl.patches[-1][1] is True, ctl.patches)
ctl.patches.clear()
panel._corner.picked.emit("top_left")
app.processEvents()
check("状态条改左上角", ctl.patches and
      ctl.patches[-1][0] == {"live": {"status_corner": "top_left"}}, ctl.patches)

# ---- 4. 滑块：显示值 vs 实际存储值 ----
ctl.patches.clear()
slider, label, sc, suffix, dec = panel._sliders[("sticker", "default_zoom")]
slider.setValue(150)                     # 显示 150%，内部存 1.5
app.processEvents()
patch = ctl.patches[-1][0] if ctl.patches else {}
value = patch.get("sticker", {}).get("default_zoom")
check("缩放滑块 150 -> 存 1.5", value == 1.5, patch)
check("缩放标签显示百分比", label.text() == "150%", label.text())

ctl.patches.clear()
slider, label, sc, suffix, dec = panel._sliders[("live", "frame_diff_threshold")]
slider.setValue(25)                      # 显示 2.5，内部存 0.25? -> 25/10=2.5
app.processEvents()
value = (ctl.patches[-1][0].get("live", {}) or {}).get("frame_diff_threshold") if ctl.patches else None
check("帧差滑块 25 -> 存 2.5 且保留一位小数", value == 2.5, value)
check("帧差标签一位小数", label.text() == "2.5", label.text())

# ---- 5. 恢复默认：只推一次 ----
ctl.patches.clear()
panel._on_reset_advanced()
app.processEvents()
check("恢复默认只推一次补丁", len(ctl.patches) == 1, len(ctl.patches))
merged = ctl.patches[0][0] if ctl.patches else {}
keys = set()
for section, values in merged.items():
    for key in values:
        keys.add((section, key))
expected = {("live", "capture_interval_ms"), ("live", "ocr_min_interval_ms"),
            ("live", "frame_diff_threshold"), ("sticker", "default_zoom"),
            ("sticker", "default_opacity"), ("ocr", "tile_max"),
            ("live", "adaptive_capture")}
check("恢复默认覆盖 7 个高级参数", keys == expected, keys ^ expected)
check("恢复默认值正确（缩放回 100%）",
      merged.get("sticker", {}).get("default_zoom") == 1.0, merged)

# ---- 6. 侧栏分页 ----
ctl.patches.clear()
panel._navs["display"].clicked.emit()
app.processEvents()
check("点侧栏切到「显示」页", panel._current == "display"
      and panel._stack.currentWidget() is panel._pages["display"], panel._current)
check("导航高亮跟随当前页",
      panel._navs["display"].is_active() and not panel._navs["home"].is_active())
check("切换分页不产生配置写回", len(ctl.patches) == 0, ctl.patches)

# ---- 6b. 角落示意图：点哪个角就贴哪个角 ----
panel._goto("display")
panel._corner.set_value("bottom_right")
app.processEvents()
ctl.patches.clear()
rect = panel._corner._corner_rects()["top_left"]
QTest.mouseClick(panel._corner, Qt.LeftButton, Qt.NoModifier,
                 QPoint(int(rect.center().x()), int(rect.center().y())))
app.processEvents()
check("点击示意图左上角 -> 状态条改左上", ctl.patches and
      ctl.patches[-1][0] == {"live": {"status_corner": "top_left"}}, ctl.patches)
check("示意图选中态同步", panel._corner.current() == "top_left",
      panel._corner.current())
panel._goto("home")

# ---- 6.5 识别规则页（V4.3：嵌在面板里，不再弹独立窗口）----
panel._navs["rules"].clicked.emit()
for _ in range(4):
    app.processEvents()
check("点侧栏「识别规则」切到 rules 页", panel._current == "rules",
      panel._current)
check("规则页是分页里的一页，不是新窗口",
      panel._stack.currentWidget() is panel._pages["rules"])
check("没有弹出任何对话框窗口",
      not [w for w in app.topLevelWidgets()
           if isinstance(w, QDialog) and w.isVisible()],
      [w.windowTitle() for w in app.topLevelWidgets() if isinstance(w, QDialog)])

rules_page = panel._rules_page
check("规则页读到了配置里的类别",
      rules_page._list.count() == len(ctl.cfg.get("rules") or []),
      rules_page._list.count())
check("刚打开时没有未保存改动", rules_page.is_dirty() is False)

_orig_words = rules_page._words.toPlainText()
rules_page._words.setPlainText("大特写\n特写")
check("改了关键词 -> 标记为有改动", rules_page.is_dirty() is True)

rules_page.reload()          # 等价于点「放弃修改」并确认
check("放弃修改后回到配置内容",
      rules_page.is_dirty() is False and
      rules_page._words.toPlainText() == _orig_words,
      rules_page._words.toPlainText()[:20])

saved_hits = []
rules_page.saved.connect(lambda: saved_hits.append(1))
ctl.patches.clear()
rules_page._words.setPlainText("大特写\n特写")
rules_page._on_save()
for _ in range(3):
    app.processEvents()
patched = ctl.patches[-1][0] if ctl.patches else {}
check("保存推的是整份 rules 补丁",
      isinstance(patched.get("rules"), list) and len(patched["rules"]) > 0,
      list(patched)[:3])
check("保存同时要求重新识别", bool(ctl.patches and ctl.patches[-1][1]) is True,
      ctl.patches[-1:] and ctl.patches[-1][1])
check("保存后不再是「有改动」状态", rules_page.is_dirty() is False)
check("保存后发出 saved 信号", saved_hits == [1], saved_hits)
check("保存后侧栏计数跟着更新",
      "类" in panel._navs["rules"]._value.text(), panel._navs["rules"]._value.text())

rules_page.reload()

# ---- 6.8 视觉细节（V4.4：滑块式选中 / 自绘按钮）----
from app.ui_widgets import AnimatedButton

check("主按钮是自绘补间按钮", isinstance(panel._btn_new, AnimatedButton),
      type(panel._btn_new).__name__)

panel._goto("capture")
for _ in range(3):
    app.processEvents()
panel.resize(panel.width() + 1, panel.height())   # 触发一次「不带动画」的对齐
for _ in range(2):
    app.processEvents()
check("侧栏选中胶囊对准当前页那一行",
      abs(panel._sidebar._hl.y() - panel._navs["capture"].geometry().y()) < 1.0,
      (panel._sidebar._hl.y(), panel._navs["capture"].geometry().y()))
check("分段控件算出了选中块",
      panel._mode._hl.width() > 0 and panel._mode.current() != "",
      (panel._mode._hl, panel._mode.current()))
panel._goto("home")

# ---- 7. 几何记忆 ----
panel.setGeometry(120, 90, 1200, 800)
ctl.patches.clear()
panel.hide()
app.processEvents()
geo = (ctl.cfg.get("ui") or {}).get("panel_geometry")
check("隐藏时记录窗口几何", isinstance(geo, list) and geo[2] == 1200 and geo[3] == 800, geo)

panel2 = ControlPanel(ctl)
check("重开还原到上次位置", panel2.geometry().x() == 120 and panel2.geometry().width() == 1200,
      panel2.geometry())
panel2.deleteLater()

# ---- 8. 交互：新建框选会先关掉面板 ----
ctl.calls.clear()
panel.show()
app.processEvents()
panel._on_new_selection()
app.processEvents()
check("新建框选交给 controller（由其负责收面板 / 取消后还原）",
      ctl.calls == ["select"], ctl.calls)

# ---- 9. 快捷键与 Esc ----
panel.show()
app.processEvents()
panel.activateWindow()
QTest.keyClick(panel, Qt.Key_Escape)
app.processEvents()
check("Esc 收起面板", not panel.isVisible())

names = [s.key().toString() for s in panel.findChildren(type(panel.findChildren.__self__))] \
    if False else None
from PySide6.QtGui import QShortcut
have = sorted(set(sc.key().toString() for sc in panel.findChildren(QShortcut)))
check("快捷键已注册（Ctrl+N / Ctrl+R / Ctrl+E）",
      have == ["Ctrl+E", "Ctrl+N", "Ctrl+R"], have)

# ---- 10. 布局不溢出（四个分页在默认尺寸下都不该出现滚动条）----
panel.show()
app.processEvents()
app.processEvents()
overflow = {}
for _key in ("home", "display", "capture", "rules", "advanced"):
    panel._goto(_key)
    for _ in range(3):
        app.processEvents()
    overflow[_key] = panel._scroll.verticalScrollBar().maximum()
check("五页默认状态均无需滚动", all(v == 0 for v in overflow.values()), overflow)
panel.resize(1120 + panel._spad * 2, 740 + panel._spad + panel._spad_b)
for _ in range(3):
    app.processEvents()
check("默认尺寸 1120x740（逻辑，不含投影留白）",
      (panel.width() - panel._spad * 2,
       panel.height() - panel._spad - panel._spad_b) == (1120, 740),
      (panel.width(), panel.height()))
check("侧栏宽度固定 214", panel._sidebar.width() == 214, panel._sidebar.width())
check("最小尺寸不小于 900x660（逻辑）",
      (panel.minimumWidth() - panel._spad * 2,
       panel.minimumHeight() - panel._spad - panel._spad_b) == (900, 660),
      (panel.minimumWidth(), panel.minimumHeight()))
check("窗口带透明底板（真圆角）",
      panel.testAttribute(Qt.WA_TranslucentBackground))
check("有投影描边覆盖层", getattr(panel, "_rim", None) is not None)
check("侧栏品牌区有程序图标", not panel._logo.pixmap().isNull(),
      panel._logo.size())
check("窗口图标已设置", not panel.windowIcon().isNull())
panel._goto("home")

panel.hide()
print()
if FAILS:
    print("失败项：", "、".join(FAILS))
    sys.exit(1)
print("全部通过")
