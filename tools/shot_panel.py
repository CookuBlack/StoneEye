# -*- coding: utf-8 -*-
"""V4.5 真机渲染校验：面板到底是不是真圆角、投影有没有画出来。

跑法：
    python tools/shot_panel.py [页面key] [输出png]

流程：开真窗口 -> 等布局/动画稳定 -> mss 抓窗户外扩一圈的画面
     -> 打印四角像素 alpha/RGB -> 存图。
"""
import ctypes
import os
import sys
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
for _f in (r"C:/Windows/Fonts/msyh.ttc",):
    if os.path.exists(_f):
        QFontDatabase.addApplicationFont(_f)
app.setFont(QFont("Microsoft YaHei UI", 9))

from app.config_manager import ConfigManager
from app.control_panel import ControlPanel


class FakeService:
    def stats(self):
        return dict(ready=True, attempted=True, last_ms=420.0, avg_ms=480.0,
                    queue=0, ocr_count=7)


class FakeManager:
    def count(self):
        return 2

    def latest_text(self):
        return "镜头缓慢向前推进，全景。"


class FakeController:
    def __init__(self):
        self.cfg = ConfigManager(
            r"D:/ProgramProject/Python/ScreenshotRecognition/config.json").load()
        self.service = FakeService()
        self.manager = FakeManager()

    def apply_panel_patch(self, patch, reocr=False):
        for section, values in patch.items():
            self.cfg.setdefault(section, {}).update(values)
        return True

    def reload_config(self):
        pass

    def open_config(self):
        pass

    def start_selection(self):
        pass

    def recognize_all(self):
        pass

    def close_all(self):
        pass


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def main():
    page = sys.argv[1] if len(sys.argv) > 1 else "home"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.environ.get("TEMP", "."), "v45_%s.png" % page)

    panel = ControlPanel(FakeController())
    panel.move(160, 90)
    # 测试脚本里窗口置顶，否则会被别的窗口压住，截出来的图看不出圆角
    panel.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    pump(400)
    panel._goto(page)
    pump(600)

    g = panel.geometry()
    print("window geometry", g.x(), g.y(), g.width(), g.height(),
          "scale", panel._scale, "pad", panel._spad, panel._spad_b)

    import mss
    from PIL import Image

    box = dict(left=g.x() - 30, top=g.y() - 30,
               width=g.width() + 60, height=g.height() + 60)
    with mss.mss() as sct:
        raw = sct.grab(box)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    img.save(out)
    print("saved", out, img.size)

    # 四角：距窗口左上角 (0,0) 的位置，30 是外扩量
    px = img.load()
    for name, (dx, dy) in (("TL", (0, 0)), ("TR", (g.width() - 1, 0)),
                           ("BL", (0, g.height() - 1)),
                           ("BR", (g.width() - 1, g.height() - 1))):
        print("corner", name, px[30 + dx, 30 + dy])
    # 底板内的实心点（离角 40px）作为对照
    print("inside TL", px[30 + 40, 30 + 40])
    print("outside 5px", px[30 - 5, 30 - 5])
    print("outside 14px", px[30 - 14, 30 - 14])
    # 左边缘外一条横线：能看出投影是不是渐隐的
    mid = 30 + g.height() // 2
    print("shadow profile L->R:",
          [px[30 - d, mid][0] for d in range(22, 0, -1)])
    panel.hide()
    pump(60)


if __name__ == "__main__":
    main()
