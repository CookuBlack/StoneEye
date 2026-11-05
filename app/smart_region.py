# -*- coding: utf-8 -*-
"""【已停用 · V3.8】智能选区：预测鼠标下最可能被框选的那块矩形。

!!! 本模块自 V3.8 起不再被任何代码引用，也不会被 PyInstaller 打包。
!!! 用户在真机上实测「总框住整个大窗口 / 单击采用不符合习惯」，决定
!!! 放弃自动预测，框选统一改成纯手动拖拽（见 region_selector.py）。
!!! 文件保留只是为了将来需要时能翻回实现；不要重新接入，除非重新设计。

微信 / QQ 截图里「鼠标移过去自动浮出一个框」的本质是命中测试：
  - EnumWindows 按 z-order 往下枚举可见顶层窗口（一次，毫秒级）；
  - 顶层窗口矩形用 DWM 的 EXTENDED_FRAME_BOUNDS，能去掉 Win10/11 的窗口投影；
  - 再顺着 ChildWindowFromPointEx 逐层往里走，拿到「内容区」
    （浏览器页面、微信聊天面板、编辑器正文…），而不是永远框住整个大窗口；
  - 鼠标移动时只在一份缓存好的矩形链里做点包含判断，没有系统调用。

注意一个坑：**子窗口不能用 DWM 的扩展边框**。DWM 只对顶层窗口有效，
对子窗口会返回带偏移的无效值（实测原点比父窗口还靠左上 48px），
所以子窗口一律用 GetWindowRect —— 子窗口本来就没有投影，不存在偏差。

拿不到系统 API（非 Windows / 调用失败）时一律返回空，
调用方退化成自由拖拽框选，不会报错。
"""
import ctypes
import sys
from collections import namedtuple
from ctypes import wintypes

# rect 是物理像素 (x, y, w, h)
WindowBox = namedtuple("WindowBox", "hwnd rect title depth")

_IS_WIN = sys.platform == "win32"

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

CWP_ALL = 0x0000

# 桌面 / 任务栏 / 输入法候选 / 系统浮层，都不该成为候选框
SKIP_CLASSES = frozenset((
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
    "Windows.UI.Core.CoreWindow", "ForegroundStaging",
    "MultitaskingViewFrame", "XamlExplorerHostIslandWindow",
    "Windows.Internal.Shell.TabProxyWindow",
    "IME", "MSCTFIME UI", "tooltips_class32",
))

# 这些子窗口类别是装饰/滚动条，框住它们没有意义
SKIP_CHILD_CLASSES = frozenset((
    "ScrollBar", "SysScrollbar", "msctls_statusbar32", "ToolbarWindow32",
    "ReBarWindow32", "SysHeader32", "msctls_progress32", "Button",
))


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG), ("top", wintypes.LONG),
        ("right", wintypes.LONG), ("bottom", wintypes.LONG),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


_user32 = None
_dwmapi = None
_bound = False
_available = False


def _bind():
    """绑定 Win32 函数并声明参数类型。

    必须显式声明：64 位下 HWND 是 8 字节指针，不声明会被截断成 int32，
    传入后变成无效句柄（表现为「一个窗口都枚举不到」）。
    """
    global _user32, _dwmapi, _bound, _available
    if _bound:
        return _available
    _bound = True
    if not _IS_WIN:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = wintypes.LONG
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                          ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                         ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowRect.argtypes = [wintypes.HWND,
                                         ctypes.POINTER(_RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = [wintypes.HWND,
                                         ctypes.POINTER(_RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [wintypes.HWND,
                                          ctypes.POINTER(_POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL
        user32.ScreenToClient.argtypes = [wintypes.HWND,
                                          ctypes.POINTER(_POINT)]
        user32.ScreenToClient.restype = wintypes.BOOL
        user32.ChildWindowFromPointEx.argtypes = [
            wintypes.HWND, _POINT, wintypes.UINT
        ]
        user32.ChildWindowFromPointEx.restype = wintypes.HWND
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND

        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

        _user32, _dwmapi = user32, dwmapi
        _available = True
    except Exception:
        _available = False
    return _available


def available() -> bool:
    return _bind()


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value.strip()


def _cloaked(hwnd) -> bool:
    """UWP 挂起 / 虚拟桌面切换后残留的窗口仍然 IsWindowVisible，但不可见。"""
    if _dwmapi is None:
        return False
    value = wintypes.DWORD(0)
    try:
        hr = _dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        return False
    return hr == 0 and value.value != 0


def _to_rect(rect):
    x, y = rect.left, rect.top
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return (int(x), int(y), int(w), int(h))


def frame_rect(hwnd, dwm: bool = True):
    """窗口在屏幕上的物理像素矩形 (x, y, w, h)；失败返回 None。

    dwm=True 仅对**顶层窗口**成立：扩展边框去掉了投影，比 GetWindowRect
    更贴合可见边缘。子窗口必须传 dwm=False，否则会拿到带偏移的错值。
    """
    if not _user32 or not hwnd:
        return None
    rect = _RECT()
    ok = False
    if dwm and _dwmapi is not None:
        try:
            ok = _dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect), ctypes.sizeof(rect),
            ) == 0
        except Exception:
            ok = False
    if not ok:
        ok = bool(_user32.GetWindowRect(hwnd, ctypes.byref(rect)))
    return _to_rect(rect) if ok else None


def client_rect(hwnd):
    """客户区矩形（不含标题栏与边框），物理像素；失败返回 None。"""
    if not _user32 or not hwnd:
        return None
    rect = _RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = _POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return (int(origin.x), int(origin.y), int(w), int(h))


def _clamp_inner(rect, outer):
    """把子窗口矩形夹进父矩形，防御少数自绘框架返回越界值。"""
    if outer is None:
        return rect
    x1 = max(rect[0], outer[0])
    y1 = max(rect[1], outer[1])
    x2 = min(rect[0] + rect[2], outer[0] + outer[2])
    y2 = min(rect[1] + rect[3], outer[1] + outer[3])
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def child_chain(hwnd, x, y, depth=4):
    """从顶层窗口往里逐层找包含该点的子控件矩形，返回 [child, grandchild, ...]。

    ChildWindowFromPointEx 一次只返回**直接**子窗口（z-order 最上面的那个），
    所以这里循环下钻，点到哪层就走到哪层。
    """
    if not _bind() or not hwnd:
        return []
    chain = []
    parent = hwnd
    outer = frame_rect(hwnd)
    for _ in range(max(1, int(depth))):
        pt = _POINT(int(x), int(y))
        if not _user32.ScreenToClient(parent, ctypes.byref(pt)):
            break
        child = _user32.ChildWindowFromPointEx(parent, pt, CWP_ALL)
        if not child or child == parent:
            break
        if _class_name(child) in SKIP_CHILD_CLASSES:
            break
        # 子窗口没有阴影，不能用 DWM 扩展边框
        rect = frame_rect(child, dwm=False)
        rect = _clamp_inner(rect, outer) if rect else None
        if rect is None:
            break
        chain.append((int(child), rect))
        parent, outer = child, rect
    return chain


def list_windows(exclude_pids=(), min_size=(48, 32)):
    """按 z-order（最上层在前）返回可见顶层窗口。"""
    if not _bind():
        return []
    own = {int(p) for p in exclude_pids}
    found = []

    def _callback(hwnd, _lparam):
        # ctypes 回调里抛出的异常会直接终止进程，必须全部吞掉
        try:
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True
            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in own:
                return True
            ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TOOLWINDOW or ex & WS_EX_TRANSPARENT:
                return True
            if _class_name(hwnd) in SKIP_CLASSES or _cloaked(hwnd):
                return True
            rect = frame_rect(hwnd)
            if rect is None or rect[2] < min_size[0] or rect[3] < min_size[1]:
                return True
            found.append(WindowBox(int(hwnd), rect, _title(hwnd), 0))
        except Exception:
            pass
        return True

    callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )(_callback)
    try:
        _user32.EnumWindows(callback, 0)
    except Exception:
        return []
    return found


def _inside(rect, x, y) -> bool:
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def pick_at(x, y, boxes):
    """返回包含该点的最上层窗口（只到窗口级）。"""
    for box in boxes:
        if _inside(box.rect, x, y):
            return box
    return None


def pick_chain(x, y, boxes, max_depth=4, min_ratio=0.12):
    """返回包含该点的窗口 + 里层内容区的矩形链，由外到内。

    每一层都要满足「面积不小于上一层的 min_ratio」，否则当作按钮 / 图标忽略，
    避免预测框在细碎控件上乱跳。
    """
    top = pick_at(x, y, boxes)
    if top is None:
        return []
    out = [top]
    area = top.rect[2] * top.rect[3]
    for hwnd, rect in child_chain(top.hwnd, x, y, max_depth):
        child_area = rect[2] * rect[3]
        if child_area < area * max(0.05, min(1.0, min_ratio)):
            continue
        out.append(WindowBox(hwnd, rect, top.title, len(out)))
        area = child_area
    return out
