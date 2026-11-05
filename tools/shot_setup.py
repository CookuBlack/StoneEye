# -*- coding: utf-8 -*-
"""安装程序界面预览（开发用）：真开窗 -> 抓图，验证版式与圆角。

    python tools/shot_setup.py <page> [out.png]

page: 0=安装位置 1=附加任务 2=正在安装
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
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

sys.path.insert(0, os.path.join(ROOT, "installer"))
import setup_app


def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def main():
    page = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.environ.get("TEMP", "."), "setup_%d.png" % page)

    win = setup_app.SetupWindow()
    win.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    win.show()
    win.raise_()
    pump(500)
    if page:
        win._goto_page(page)
        pump(300)
    if page == 2:
        win._big.setText("正在安装…")
        win._bar.set_value(0.62)
        win._detail.setText("_internal/PySide6/Qt6Core.dll    62%")
        pump(150)

    g = win.geometry()
    import mss
    from PIL import Image

    box = dict(left=g.x() - 12, top=g.y() - 12,
               width=g.width() + 24, height=g.height() + 24)
    with mss.mss() as sct:
        raw = sct.grab(box)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    img.save(out)
    print("saved", out, img.size, "size", g.width(), g.height())
    win.close()
    pump(60)


if __name__ == "__main__":
    main()
