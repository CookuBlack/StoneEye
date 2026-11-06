# -*- coding: utf-8 -*-
"""手动滚轮截长图（V4.9.2）。

之前的实现自己用 mouse_event 连发 8 次滚轮、固定抓 8 帧，页面滚多少、
什么时候到底全都由程序猜。现在反过来：**滚动完全交给用户**。

流程：点「截长图」-> 编辑器与标注浮窗全部隐藏（否则会被拍进画面）
-> 只留一个独立的 topmost 小控制条 -> 用户把鼠标停在截图区域里，
自己向下滚滚轮 -> 每 130ms 采一帧，和上一帧做重叠行匹配，只把
新露出来的底部若干行接到长图上 -> 用户点「完成拼接」/「取消」收工。

为什么不抓滚轮事件：滚轮是发给鼠标下面的那个应用窗口的，编辑器已经
藏了，装全局钩子既容易拖慢系统又容易被安全软件误报。定时采样 +
「静帧直接跳过」在不动的时候几乎零开销，滚动时也能跟上。

HUD 特意**不挂编辑器 owner**：Windows 在 owner 被 hide 时会把它的
owned window 一起藏掉，而长图模式恰恰必须把编辑器藏起来。
"""
import ctypes
import sys

import numpy as np

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .brand import brand_hex
from .capture_toolbar import FLOAT_FLAGS, ToolSurface
from .screen_capture import capture
from .scroll_capture import best_shift

SAMPLE_MS = 130
MAX_HEIGHT = 24000


def _changed(a: np.ndarray, b: np.ndarray, threshold: float = 1.2) -> bool:
    """抽稀比较两帧；画面没动就别去做昂贵的重叠匹配。

    采样步长不能按「整幅图取 24 个点」来算：一行文字常常只有 18px 高，
    步长 25px 会把整行文字全跳过去，用户只滚三行时判成静帧，那一段
    内容就永远丢了（真实事件循环回归里复现过）。改成每 4px 取一点，
    整幅覆盖，既能看出 3px 的小滚动，也能认出滚到底后的完全静止。
    """
    if a.shape != b.shape:
        return True
    # 整幅每 4px 取一点，比「按尺寸抽 64 个点」覆盖得全：等行高文档
    # 里抽稀点可能全落在空白处，明明滚到底了却还在继续拼重复内容。
    dif = np.abs(a[::4, ::4].astype(np.int16)
                 - b[::4, ::4].astype(np.int16))
    return float(dif.mean()) > threshold


class ManualLongCapture:
    """一次手动滚动会话：只保留「上一帧 + 已拼好的长图」两块像素。"""

    def __init__(self, rect, frame_source=None, max_height=MAX_HEIGHT):
        self.rect = rect
        self._source = frame_source or self._grab
        self._move_mouse = frame_source is None
        self.max_height = int(max_height)
        self.stitched = None
        self._last = None
        self._cursor_home = None
        self.segments = 0
        self.gained = 0
        self.limited = False

    def _grab(self):
        return np.array(capture(self.rect).convert("RGB"), copy=True)

    # ---------- 鼠标 ----------
    def _park_cursor(self):
        if not self._move_mouse or sys.platform != "win32":
            return
        try:
            from ctypes import wintypes
            u = ctypes.windll.user32
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            self._cursor_home = (pt.x, pt.y)
            # 把鼠标放到选区中间，滚轮才会落在要截的那个窗口上
            u.SetCursorPos(int(self.rect.x + self.rect.w / 2),
                           int(self.rect.y + self.rect.h / 2))
        except Exception:
            self._cursor_home = None

    def release(self):
        """恢复鼠标位置（幂等，finish 和异常收尾都会调）。"""
        home = self._cursor_home
        self._cursor_home = None
        self._move_mouse = False
        if home is not None and sys.platform == "win32":
            try:
                ctypes.windll.user32.SetCursorPos(home[0], home[1])
            except Exception:
                pass
        self._last = None

    # ---------- 会话 ----------
    def begin(self):
        # 重叠匹配至少要 80 行可比像素；选区太矮时不猜，直接说清楚
        if getattr(self.rect, "h", 0) < 100:
            raise ValueError("选区太矮了，请框一个至少 100px 高的区域")
        self._park_cursor()
        first = self._source()
        self.stitched = first
        self._last = first
        return first.shape

    @property
    def width(self):
        return 0 if self.stitched is None else int(self.stitched.shape[1])

    @property
    def height(self):
        return 0 if self.stitched is None else int(self.stitched.shape[0])

    def poll(self) -> int:
        """采一帧；返回本次新接上的行数（0 = 没滚动 / 没匹配上）。"""
        if self.stitched is None or self.limited:
            return 0
        cur = self._source()
        prev = self._last
        if cur.shape != prev.shape:
            self._last = cur        # 窗口尺寸变了，别拿两幅不同宽的图去比
            return 0
        if not _changed(prev, cur):
            # 静帧时不推进参考帧：小幅滚动如果没被这一帧看出来，
            # 下一次会连着前一次一起算位移，内容不会丢。
            return 0
        dy = best_shift(prev, cur)
        self._last = cur            # 比过之后才推进，避免拿最早的帧反复比
        if dy is None or dy < 2:
            return 0
        room = self.max_height - self.stitched.shape[0]
        if room <= 0:
            self.limited = True
            return 0
        add = cur[cur.shape[0] - min(dy, room):]
        self.stitched = np.vstack((self.stitched, add))
        self.segments += 1
        self.gained += int(add.shape[0])
        if self.stitched.shape[0] >= self.max_height:
            self.limited = True
        return int(add.shape[0])

    def finish(self):
        """结束会话，返回 (PIL.Image, 拼接段数, 新增高度)。"""
        from PIL import Image
        if self.stitched is None:
            raise ValueError("长图会话还没开始")
        img = Image.fromarray(self.stitched, "RGB")
        self.release()
        return img, self.segments, self.gained


class LongCaptureHUD(ToolSurface):
    """长图模式唯一留在屏幕上的浮窗：进度 + 完成 / 取消。

    独立顶层窗口、topmost、不抢焦点 —— 不抢焦点很关键，否则用户滚轮
    会滚到这个浮窗上而不是底下的网页 / 文档。
    """

    finishClicked = Signal()
    cancelClicked = Signal()

    RADIUS = 13

    def __init__(self, parent=None):
        super().__init__(parent, FLOAT_FLAGS)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("滚动截长图")
        lay = QVBoxLayout(self)
        m = self.content_margins()
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(self._px(9))

        head = QWidget()
        head.setAttribute(Qt.WA_NoSystemBackground, True)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(2, 0, 0, 0)
        hl.setSpacing(self._px(8))
        dot = QLabel()
        d = self._px(8)
        dot.setFixedSize(d, d)
        dot.setStyleSheet("background:%s;border-radius:%dpx;"
                          % (brand_hex(), d // 2))
        title = QLabel("滚动截长图")
        title.setStyleSheet(
            "color:#F2F5FA;font-weight:700;font-size:15px;"
            "background:transparent;")
        tag = QLabel("手动滚轮")
        tag.setStyleSheet(
            "color:%s;background:rgba(255,255,255,20);border-radius:7px;"
            "padding:2px 8px;font-size:11px;font-weight:600;" % brand_hex())
        hl.addWidget(dot)
        hl.addWidget(title)
        hl.addStretch(1)
        hl.addWidget(tag)
        lay.addWidget(head)

        self._stat = QLabel()
        self._stat.setMinimumWidth(self._px(252))
        self._stat.setStyleSheet(
            "color:#F2F5FA;font-weight:700;font-size:14px;"
            "background:transparent;")
        lay.addWidget(self._stat)
        self._tip = QLabel()
        self._tip.setStyleSheet(
            "color:#AAB5C5;font-size:12px;background:transparent;")
        lay.addWidget(self._tip)

        row = QHBoxLayout()
        row.setSpacing(self._px(8))
        row.addStretch(1)
        self._btn_cancel = QPushButton("取消")
        self._btn_finish = QPushButton("完成拼接")
        self._btn_cancel.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,18);color:#DCE3EE;"
            "border:1px solid rgba(255,255,255,30);border-radius:8px;"
            "padding:7px 18px;font-size:13px;}"
            "QPushButton:hover{background:rgba(255,255,255,46);color:#FFFFFF;}")
        self._btn_finish.setStyleSheet(
            "QPushButton{background:%s;color:#101820;border:0;"
            "border-radius:8px;padding:7px 20px;font-weight:700;font-size:13px;}"
            "QPushButton:hover{background:#EAF6FF;}" % brand_hex())
        for b in (self._btn_cancel, self._btn_finish):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            row.addWidget(b)
        lay.addLayout(row)
        # QPushButton.clicked 带一个 checked 参数，别直接连到无参 Signal.emit
        self._btn_finish.clicked.connect(
            lambda checked=False: self.finishClicked.emit())
        self._btn_cancel.clicked.connect(
            lambda checked=False: self.cancelClicked.emit())

        self.set_status(0, 0, 0)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.ensure_rim()

    def set_status(self, w, h, segments, limited=False, gained=0):
        # 这行是固定宽度窗口里最长的文本，别再往后面堆单位，
        # 否则末尾会被裁掉（预览里 "+1080 px" 的 px 就曾消失）。
        if segments:
            self._stat.setText("已拼接 %d × %d  ·  %d 段"
                               % (w, h, segments))
        else:
            self._stat.setText("已抓取 %d × %d  ·  等待滚动" % (w, h))
        self._stat.setToolTip("累计新增 %d px" % gained if gained else "")
        if limited:
            self._tip.setText("已达到长度上限，点「完成拼接」生成长图")
        elif segments:
            self._tip.setText("继续向下滚动，或点「完成拼接」结束")
        else:
            self._tip.setText("鼠标已在截图区域中心 · 慢慢向下滚动滚轮")

    def place_outside(self, sel: QRect, screen: QRect) -> bool:
        """尽量摆到选区外，免得被抓进长图。返回是否仍与选区相交。"""
        gap = self._px(12)
        w, h = self.width(), self.height()
        candidates = (
            (sel.center().x() - w // 2, sel.bottom() + gap),
            (sel.center().x() - w // 2, sel.top() - h - gap),
            (screen.right() - w - gap, screen.top() + gap),
            (screen.left() + gap, screen.top() + gap),
            (screen.right() - w - gap, screen.bottom() - h - gap),
            (screen.left() + gap, screen.bottom() - h - gap),
        )
        fallback = None
        for x, y in candidates:
            r = QRect(x, y, w, h)
            if not screen.contains(r):
                continue
            if not r.intersects(sel):
                self.move(x, y)
                return False
            if fallback is None:
                fallback = (x, y)
        if fallback is None:
            fallback = (
                max(screen.left() + gap,
                    min(candidates[0][0], screen.right() - w - gap)),
                max(screen.top() + gap,
                    min(candidates[0][1], screen.bottom() - h - gap)))
        self.move(fallback[0], fallback[1])
        return True
