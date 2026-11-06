# -*- coding: utf-8 -*-
"""截长图：自动向下滚滚轮 -> 逐帧抓屏 -> 按重叠行拼接成一张长图。

拼接不依赖任何第三方 CV 库：把两帧按「上一帧第 d 行起」和「下一帧第 0 行起」
逐行比灰度差，取误差最小的 d 就是滚动距离；新内容就是下一帧的最后 d 行。
列方向降采样到 1/4，8 帧 × 200 个候选位移也就几毫秒。

滚轮用 Win32 mouse_event 发到鼠标所在窗口（不用 pyautogui，少一个依赖）。
调用方必须在抓帧期间把编辑器 / 工具条全部藏起来，否则会把工具条拍进去。
"""
import ctypes
import sys
import time

import numpy as np

from .screen_capture import capture

MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


def _wheel(user32, clicks=3):
    data = (-WHEEL_DELTA * clicks) & 0xFFFFFFFF
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, data, 0)


def _cursor(user32):
    from ctypes import wintypes
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def best_shift(prev: np.ndarray, cur: np.ndarray):
    """返回 cur 相对 prev 向下滚动的像素数；找不到可靠重叠返回 None。"""
    h = min(prev.shape[0], cur.shape[0])
    if h < 80:
        return None
    a = prev[:, ::4].astype(np.int16)
    b = cur[:, ::4].astype(np.int16)
    limit = min(h - 40, 480)
    best, best_dy = 1e9, None
    for dy in range(8, limit, 2):
        length = min(h - dy, 220)
        if length < 24:
            break
        dif = np.abs(a[dy:dy + length] - b[:length]).mean()
        if dif < best:
            best, best_dy = dif, dy
    return best_dy if best < 22.0 else None


def stitch(frames):
    """frames: [RGB ndarray] -> 单张长图 ndarray。"""
    if not frames:
        raise ValueError("没有可用帧")
    out = frames[0]
    for cur in frames[1:]:
        # 只拿上一帧高度的「底部」去比，长图顶部的旧内容不参与匹配
        tail = out[-cur.shape[0]:]
        dy = best_shift(tail, cur)
        if dy is None or dy < 8:
            break                    # 到底了 / 页面没动，别把重复内容接上去
        out = np.vstack((out, cur[cur.shape[0] - dy:]))
    return out


def capture_long(rect, steps=8, delay=0.28, progress=None):
    """滚动抓帧并拼接，返回 (PIL.Image, 实际帧数)。

    progress(done, total) 每抓完一帧回调一次；回调里不要弹 UI。
    """
    if sys.platform != "win32":
        raise RuntimeError("截长图目前只支持 Windows")

    user32 = ctypes.windll.user32
    old_x, old_y = _cursor(user32)
    # 把鼠标放到选区中间，滚轮才会落在目标窗口上
    target_x = int(rect.x + rect.w / 2)
    target_y = int(rect.y + rect.h / 2)
    user32.SetCursorPos(target_x, target_y)

    frames = []
    try:
        for i in range(max(1, steps)):
            img = capture(rect)
            frames.append(np.array(img.convert("RGB"), copy=True))
            if progress:
                progress(i + 1, steps)
            if i + 1 < steps:
                _wheel(user32, 3)
                # 等页面真的滚完；Qt 事件循环还得转，否则窗口收不到滚轮
                from PySide6.QtWidgets import QApplication
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    QApplication.processEvents()
                    time.sleep(0.008)
    finally:
        user32.SetCursorPos(old_x, old_y)

    from PIL import Image
    arr = stitch(frames)
    return Image.fromarray(arr, "RGB"), len(frames)
