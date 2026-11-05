# -*- coding: utf-8 -*-
"""Qt 逻辑像素 <-> 屏幕物理像素 的换算。

首选策略：main.py 用 QT_ENABLE_HIGHDPI_SCALING=0 让两者 1:1，
此时本模块算出的比例恒为 1.0，代码完全退化为文档里的「统一物理像素」设计。

兜底策略：某些 Qt 版本 / 运行环境可能忽略该开关，此时按
「Win32 虚拟桌面物理宽度 / Qt 虚拟桌面逻辑宽度」求出比例并换算，
保证 150% DPI 下截图区域、贴图位置、高亮框仍然对齐。
"""
import sys

_scale = None
_zoom = None

SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def ui_scale() -> float:
    """返回 物理像素 / Qt 逻辑像素（>=1，正常情况下为 1.0）。"""
    global _scale
    if _scale is not None:
        return _scale
    scale = 1.0
    if sys.platform == "win32":
        try:
            import ctypes
            from PySide6.QtGui import QGuiApplication

            user32 = ctypes.windll.user32
            # 进程为 Per-Monitor V2 DPI aware 时，GetSystemMetrics 返回物理像素
            phys_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            phys_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            screens = list(QGuiApplication.screens())
            if screens and phys_w > 0 and phys_h > 0:
                left = min(s.geometry().left() for s in screens)
                top = min(s.geometry().top() for s in screens)
                right = max(s.geometry().right() for s in screens)
                bottom = max(s.geometry().bottom() for s in screens)
                logi_w = right - left + 1
                logi_h = bottom - top + 1
                if logi_w > 0 and logi_h > 0:
                    sx, sy = phys_w / logi_w, phys_h / logi_h
                    # 小偏差当浮点噪声忽略，只有真的存在缩放才启用换算
                    if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01:
                        scale = max(0.1, (sx + sy) / 2.0)
        except Exception:
            pass
    _scale = scale
    return _scale


def _applied_dpi(default: int = 96) -> int:
    """从注册表读系统缩放（120 = 125%，144 = 150%）。

    GetDpiForSystem 对「非 DPI 感知进程」恒返回 96，而本模块可能在
    main.py 调 SetProcessDpiAwareness 之前就被 import 到，所以留这条路。
    """
    try:
        import ctypes
        from ctypes import wintypes

        size = wintypes.DWORD(4)
        buf = wintypes.DWORD(0)
        ret = ctypes.windll.advapi32.RegGetValueW(
            wintypes.HKEY_CURRENT_USER,
            r"Control Panel\Desktop\WindowMetrics",
            "AppliedDPI", 0x00000010,     # RRF_RT_REG_DWORD
            ctypes.byref(size), ctypes.byref(buf))
        if ret == 0 and buf.value > 0:
            return int(buf.value)
    except Exception:
        pass
    return default


def ui_zoom() -> float:
    """界面放大倍数（>=1），只用于「面板 / 对话框」这类纯 UI 尺寸。

    和 ui_scale() 的区别很重要：

      ui_scale() = 物理像素 / Qt 逻辑像素，因为主程序关了 Qt 的 high-DPI
                   缩放，它恒等于 1.0，用来换算截图坐标；
      ui_zoom()  = 系统 DPI / 96，125% 屏上是 1.25。

    关掉 Qt 缩放后，12px 的字就是 12 个物理像素，在 125% 屏上比系统里
    其它程序小一圈（用户原话：「为什么对比起来这么小」）。面板不参与
    任何截图坐标换算，所以直接乘 ui_zoom() 就是最省事的正解。
    """
    global _zoom
    if _zoom is not None:
        return _zoom
    zoom = 1.0
    if sys.platform == "win32":
        try:
            import ctypes
            try:                       # Win10 1607+：按系统 DPI
                dpi = int(ctypes.windll.user32.GetDpiForSystem())
            except Exception:          # 老系统退化成 96/120/144
                hdc = ctypes.windll.user32.GetDC(0)
                dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)) or 96
                ctypes.windll.user32.ReleaseDC(0, hdc)
            if dpi <= 96:
                dpi = _applied_dpi()       # 进程还没 DPI 感知时系统只回 96
            if dpi > 0:
                zoom = max(1.0, dpi / 96.0)
        except Exception:
            pass
    _zoom = zoom
    return _zoom


def to_physical(x, y, w, h):
    """Qt 逻辑矩形 -> 物理像素矩形 (x, y, w, h)，用于 mss 截图。"""
    s = ui_scale()
    return (
        round(x * s), round(y * s),
        max(1, round(w * s)), max(1, round(h * s)),
    )


def to_logical(x, y):
    """物理像素位置 -> Qt 逻辑位置，用于窗口 move()。"""
    s = ui_scale()
    return (round(x / s), round(y / s))
