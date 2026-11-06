# -*- coding: utf-8 -*-
"""真实桌面验证编辑态各浮窗的 Z 序（不依赖抓屏像素）。

用法：
    python tools/check_zorder.py            # 打印诊断
    python tools/check_zorder.py --assert   # 任一项不达标就 exit 1

背景：编辑器是全屏 topmost 窗口，工具条/选项条/表情板是另外几个 topmost
顶层窗口。用户点过附属窗口再点回画布时，Windows 会把编辑器提到同组 Z 序
最顶，把整条工具条盖住 —— 看到的就是「点一下，控制横条消失了」。
修复方式是把附属窗口登记成编辑器的 owned window，Windows 从此保证
owned window 永远在 owner 之上。

这里用 WindowFromPoint 直接问系统「这个坐标上最上面的窗口是谁」，
比截图可靠（本机会话桌面抓屏全黑也能测）。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.screen_capture import Rect  # noqa: E402
from app.screenshot_editor import ScreenshotEditor  # noqa: E402

u = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


u.WindowFromPoint.argtypes = [POINT]
u.WindowFromPoint.restype = wintypes.HWND
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
u.GetWindow.restype = wintypes.HWND
u.SetForegroundWindow.argtypes = [wintypes.HWND]
u.SetForegroundWindow.restype = wintypes.BOOL

GW_OWNER = 4


def hwnd(widget):
    return int(widget.winId()) & 0xFFFFFFFFFFFFFFFF


def hit(x, y):
    return int(u.WindowFromPoint(POINT(int(x), int(y))) or 0) & 0xFFFFFFFFFFFFFFFF


def title(h):
    b = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(h, b, 256)
    return b.value or hex(h)


def pump(app, ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.004)


def main():
    app = QApplication(sys.argv)
    pm = QPixmap(1920, 1080)
    pm.fill(QColor(122, 30, 30))
    ed = ScreenshotEditor(pm, Rect(0, 0, 1920, 1080), Rect(420, 300, 880, 460))
    ed.show_editor()                      # 走生产代码路径，不进局部事件循环
    pump(app, 350)

    ed_h = hwnd(ed)
    bar, opt, picker = ed._toolbar, ed._options, ed._picker
    results = []

    def children(target=None):
        e = target or ed
        return [("工具条", e._toolbar), ("选项条", e._options),
                ("表情板", e._picker), ("OCR面板", e._ocr_panel)]

    def probe(tag, target=None):
        e = target or ed
        eh = hwnd(e)
        ok = True
        detail = []
        known = {eh: "编辑器"}
        for name, w in children(e):
            if w is not None and w.isVisible():
                known[hwnd(w)] = name
        for name, w in children(e):
            if w is None or not w.isVisible():
                continue
            c = w.geometry().center()
            h = hit(c.x(), c.y())
            good = (h == hwnd(w))
            owner = int(u.GetWindow(hwnd(w), GW_OWNER) or 0) & 0xFFFFFFFFFFFFFFFF
            detail.append("%s:%s%s" % (
                name, "OK" if good else "被「%s」遮挡" % known.get(h, title(h)),
                "" if owner == eh else "(owner 丢失)"))
            if not good or owner != eh:
                ok = False
                print("    [诊断] %s geometry=%s 命中=%s(%s)" % (
                    name, (w.x(), w.y(), w.width(), w.height()),
                    known.get(h, title(h)), h))
        print("%-20s %s" % (tag, "  ".join(detail)))
        return ok

    def activate_editor():
        u.SetForegroundWindow(ed_h)
        ed.activateWindow()
        pump(app, 220)

    results.append(("初始", probe("初始")))
    print("编辑器 hwnd =", ed_h, "工具条 hwnd =", hwnd(bar))

    # 1) 激活编辑器（Windows 会把它提到 topmost 组最顶）
    activate_editor()
    results.append(("激活编辑器后", probe("激活编辑器后")))

    # 2) 真的在画布上画一个矩形
    pt = QPointF(ed._sel.center())
    for typ in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        app.sendEvent(ed, QMouseEvent(typ, pt, pt, Qt.LeftButton,
                                      Qt.LeftButton, Qt.NoModifier))
    pump(app, 220)
    results.append(("画布绘制后", probe("画布绘制后")))

    # 3) 点选项条换颜色，再点回画布 —— 用户复现 bug 的那条路径
    u.SetForegroundWindow(hwnd(opt))
    pump(app, 180)
    results.append(("点选项条后", probe("点选项条后")))
    activate_editor()
    results.append(("再回编辑器", probe("再回编辑器")))

    # 4) 点工具条按钮切工具，再回画布
    QTest.mouseClick(bar._buttons["ellipse"], Qt.LeftButton)
    pump(app, 150)
    results.append(("切工具后", probe("切工具后")))
    activate_editor()
    results.append(("切工具回编辑器", probe("切工具回编辑器")))

    # 4.5) 保存对话框取消 / 截长图返回：整组窗口曾被 hide 过
    ed._hide_children()
    ed.hide()
    pump(app, 120)
    ed._resume()
    pump(app, 200)
    activate_editor()
    results.append(("弹窗返回后", probe("弹窗返回后")))

    # 5) 切到表情工具（选项条自动收起、表情板弹出）+ OCR 结果面板同开
    QTest.mouseClick(bar._buttons["sticker"], Qt.LeftButton)
    pump(app, 150)
    from app.screenshot_editor import OcrTextPanel
    ed._ocr_panel = OcrTextPanel("测试文字")
    # 面板美化后变大，塞在选区里会和表情板物理重叠；这一项验的是
    # 「多个 owned window 同开时层级都对」，所以把它挪到不重叠的左上角。
    ed._ocr_panel.move(20, 20)
    ed._show_child(ed._ocr_panel)
    activate_editor()
    results.append(("表情板+OCR同开", probe("表情板+OCR同开")))

    # 6) 按钮在不抢焦点的窗口上仍然能点
    QTest.mouseClick(bar._buttons["text"], Qt.LeftButton)
    pump(app, 120)
    btn_ok = (ed._tool == "text")
    print("%-20s 工具条按钮点击 %s" % ("按钮可用", "OK" if btn_ok else "失效"))
    results.append(("按钮可用", btn_ok))

    # 6.5) V4.9.3：真的拖一次右下手柄改选区，看裁切框同步 + 浮窗重新摆位
    from app.brand import selection_grips
    grip = selection_grips(ed._sel)["se"]

    def send_mouse(typ, pt):
        app.sendEvent(ed, QMouseEvent(typ, pt, pt, Qt.LeftButton,
                                      Qt.LeftButton, Qt.NoModifier))

    p0 = QPointF(grip.x(), grip.y())
    p1 = QPointF(grip.x() + 60, grip.y() + 40)
    before = (ed._sel.width(), ed._sel.height())
    send_mouse(QEvent.MouseButtonPress, p0)
    send_mouse(QEvent.MouseMove, p1)
    send_mouse(QEvent.MouseButtonRelease, p1)
    pump(app, 220)
    grew = (ed._sel.width() > before[0] and ed._sel.height() > before[1]
            and ed._crop.width() == ed._sel_phys.w
            and ed._crop.height() == ed._sel_phys.h)
    print("%-20s %dx%d -> %dx%d %s" % (
        "拖手柄改选区", before[0], before[1],
        ed._sel.width(), ed._sel.height(), "OK" if grew else "几何不同步"))
    results.append(("拖手柄改选区", grew))
    activate_editor()
    results.append(("改选区后浮窗", probe("改选区后浮窗")))

    ed._hide_children()
    ed.hide()
    ed.cleanup()
    pump(app, 120)

    # 7) 二次编辑：用一张现成的图开编辑态，真实窗口走一遍（不进局部事件循环）
    prev = QPixmap(420, 260)
    prev.fill(QColor(245, 245, 245))
    re = ScreenshotEditor.for_image(prev, {})
    re.show_editor()
    pump(app, 250)
    results.append(("二次编辑初始", probe("二次编辑初始", re)))
    u.SetForegroundWindow(hwnd(re))
    re.activateWindow()
    pump(app, 220)
    results.append(("二次编辑激活后", probe("二次编辑激活后", re)))
    out = re._compose()
    size_ok = (out.width(), out.height()) == (420, 260)
    print("%-20s 输出 %dx%d %s" % (
        "二次编辑合成", out.width(), out.height(), "OK" if size_ok else "尺寸错"))
    results.append(("二次编辑合成", size_ok))
    re._hide_children()
    re.hide()
    re.cleanup()
    pump(app, 120)

    bad = [name for name, ok in results if not ok]
    print("\n结论:", "全部通过" if not bad else "失败项 → " + ", ".join(bad))
    if "--assert" in sys.argv and bad:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
