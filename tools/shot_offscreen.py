# -*- coding: utf-8 -*-
"""离屏渲染截图（无桌面依赖）：用于验证版式、圆角、投影。

    python tools/shot_offscreen.py setup 0  -> 安装向导第 0 页
    python tools/shot_offscreen.py panel    -> 控制面板
"""
import ctypes
import os
import sys
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def _fake_controller():
    """面板要一个 controller 才能构造；截图只需要它能站住，不需要真干活。"""
    from app.config_manager import ConfigManager

    class Service:
        def stats(self):
            return dict(ready=True, attempted=True, last_ms=420.0,
                        avg_ms=480.0, queue=0, ocr_count=7)

    class Manager:
        def count(self):
            return 2

        def latest_text(self):
            return "镜头缓慢向前推进，全景。"

    class Controller:
        def __init__(self):
            self.config_mgr = ConfigManager(os.path.join(ROOT, "config.json"))
            self.cfg = self.config_mgr.load()
            self.service = Service()
            self.manager = Manager()

        def __getattr__(self, name):
            return lambda *a, **k: None

    return Controller()


def composite(widget, out, bg=(236, 238, 242)):
    pm = widget.grab()
    canvas = QPixmap(pm.size())
    canvas.fill(QColor(*bg))
    p = QPainter(canvas)
    p.drawPixmap(0, 0, pm)
    p.end()
    canvas.save(out)
    print("saved", out, canvas.width(), canvas.height())


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "setup"
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
        os.environ.get("TEMP", "."), "off_%s%d.png" % (kind, page))

    if kind == "setup":
        sys.path.insert(0, os.path.join(ROOT, "installer"))
        import setup_app
        win = setup_app.SetupWindow()
        win.show()
        pump(400)
        if page in (1, 2):
            win._goto_page(page)
            pump(250)
        elif page == 3:
            win._goto_page(2)
            pump(200)
        if page == 2:
            win._big.setText("正在解压文件…")
            win._bar.set_value(0.62)
            win._detail.setText("_internal/PySide6/Qt6Core.dll    62%")
            pump(120)
        elif page == 3:  # 完成态
            win._title.setText("安装完成")
            win._steps[2].set_state("done")
            win._big.setText("装好了")
            win._bar.set_value(1.0)
            win._detail.setText("装在 %s" % win._target)
            win._done_row.setVisible(True)
            pump(120)
    else:
        from app.control_panel import ControlPanel
        win = ControlPanel(_fake_controller())
        win.show()
        pump(600)

    composite(win, out)
    win.close()
    pump(50)


if __name__ == "__main__":
    main()
