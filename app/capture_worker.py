# -*- coding: utf-8 -*-
"""单个实时窗口的采集线程：持续截取固定屏幕区域 + 帧差判断。

这里只做轻活（mss 截图 + 降采样差分），绝不跑 OCR，
所以画面刷新不会被识别耗时拖慢；识别交给 RecognitionService 的独立线程。

V3.4 性能改进：
  1. 用 capture_rgb() 直出 ndarray，省掉 PIL 往返与全图 convert；
  2. 帧差「一次 abs 同时算平均差与变化点数」，原本要遍历两遍；
  3. **画面没变就不发 frame_ready** —— 主线程不再每 100ms 重建一次
     整幅 QPixmap（这是空闲时最大的 CPU/GPU 开销）；
  4. 连续静止时自动降低采集频率（adaptive_capture），回到桌面待机时
     CPU 占用从「一直抓屏」降到「偶尔确认一下」；一旦有变化立刻恢复。
"""
import time

import numpy as np
from PySide6.QtCore import QThread, Signal

from .screen_capture import capture_rgb, release


def sample_frame(array, step):
    """降采样成小数组，帧差比较只需极低开销。"""
    return array[::step, ::step, :].astype(np.int16)


def diff_metrics(previous, current, threshold=12):
    """一次 abs 同时得到 (平均绝对差, 变化采样点数)。

    只靠平均差会漏掉「一整屏里改了一个字」这种局部变化：
    一个汉字在 step=4 的降采样图上约 9 个点，平均差只有 0.07 左右，
    远低于任何合理阈值；变化点数能兜住这种情况。

    无基线或形状变化时返回 (inf, 极大值)，视为「必须刷新」。
    """
    if previous is None or previous.shape != current.shape:
        return float("inf"), 10 ** 9
    delta = np.abs(current - previous)
    mean = float(delta.mean())
    changed = int((delta.mean(axis=2) > threshold).sum())
    return mean, changed


def frame_diff(previous, current):
    """两帧平均绝对差；无基线或形状变化时返回 inf（视为变化）。"""
    return diff_metrics(previous, current)[0]


def changed_pixels(previous, current, threshold=12):
    """变化采样点数（局部小改动也能触发重识别）。"""
    return diff_metrics(previous, current, threshold)[1]


def make_frame(window_id, frame_id, image):
    """PIL.Image -> 可跨线程传递的纯数据帧（不含任何 Qt 对象）。"""
    rgb = image.convert("RGB")
    return {
        "window_id": int(window_id),
        "frame_id": int(frame_id),
        "rgb": rgb.tobytes(),
        "width": rgb.width,
        "height": rgb.height,
        "timestamp": time.monotonic(),
    }


def make_frame_from_rgb(window_id, frame_id, rgb_array):
    """ndarray -> 帧字典；只做一次 tobytes，避免再包一层 PIL。"""
    height, width = int(rgb_array.shape[0]), int(rgb_array.shape[1])
    return {
        "window_id": int(window_id),
        "frame_id": int(frame_id),
        "rgb": rgb_array.tobytes(),
        "width": width,
        "height": height,
        "timestamp": time.monotonic(),
    }


def frame_to_image(frame):
    from PIL import Image
    return Image.frombytes(
        "RGB", (frame["width"], frame["height"]), frame["rgb"]
    )


class CaptureWorker(QThread):
    frame_ready = Signal(object)   # 画面有变化（或首帧）时才发 -> 主线程重绘
    changed = Signal(object)       # 仅画面变化时发 -> OCR 队列
    failed = Signal(int, str)      # window_id, message

    def __init__(self, window_id, rect, live_cfg, parent=None):
        super().__init__(parent)
        self.window_id = int(window_id)
        self.rect = rect
        self.interval_ms = int(live_cfg.get("capture_interval_ms", 100))
        self.threshold = float(live_cfg.get("frame_diff_threshold", 1.0))
        self.step = max(1, int(live_cfg.get("diff_sample_step", 4)))
        min_changed = int(live_cfg.get("diff_min_changed_pixels", 6))
        self.min_changed = min_changed if min_changed > 0 else 10 ** 9
        # 静止时降频：最多放到 interval*4，且不超过 idle_capture_interval_ms
        self.adaptive = bool(live_cfg.get("adaptive_capture", True))
        self.idle_interval_ms = int(live_cfg.get("idle_capture_interval_ms", 400))
        self._idle_streak = 0
        self._baseline = None
        self._frame_id = 0
        self._stopped = False
        self._paused = False
        # 轻量统计，供控制面板显示（失败/帧率，不做任何锁）
        self.frames = 0
        self.changes = 0

    def set_paused(self, paused):
        self._paused = bool(paused)

    def stop(self):
        self._stopped = True
        self.wait(2000)

    def _diff_changed(self, array) -> bool:
        """与「上次判定为变化的帧」比较，捕捉缓慢累积与局部文字变化。"""
        small = sample_frame(array, self.step)
        mean, changed = diff_metrics(self._baseline, small, 12)
        if (not np.isfinite(mean)
                or mean >= self.threshold
                or changed >= self.min_changed):
            self._baseline = small
            return True
        return False

    def _sleep(self, seconds):
        """可中断的等待，stop() 后能立刻退出。"""
        deadline = time.monotonic() + seconds
        while not self._stopped:
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            self.msleep(max(1, min(20, int(remain * 1000))))

    def _next_interval_ms(self, changed: bool) -> int:
        """静止越久，采样越稀疏；任何变化立刻回到正常频率。"""
        if changed:
            self._idle_streak = 0
            return self.interval_ms
        self._idle_streak += 1
        if not self.adaptive or self._idle_streak < 3:
            return self.interval_ms
        idle = min(self.idle_interval_ms, self.interval_ms * 4)
        return max(self.interval_ms, idle)

    def run(self):
        try:
            self._loop()
        finally:
            # 必须在**采集线程内**释放，mss 实例缓存在 threading.local 里，
            # 从主线程调用 release() 释放的是主线程那一份，起不到作用。
            release()

    def _loop(self):
        while not self._stopped:
            started = time.monotonic()
            if self._paused:
                self._sleep(self.interval_ms / 1000.0)
                continue
            try:
                array = capture_rgb(self.rect)      # 物理像素 Rect
                self._frame_id += 1
                self.frames += 1
                is_changed = self._diff_changed(array)

                if is_changed:
                    # 首帧基线为空，diff 必然判定为变化，所以这里已经覆盖首帧
                    self.changes += 1
                    frame = make_frame_from_rgb(
                        self.window_id, self._frame_id, array
                    )
                    self.frame_ready.emit(frame)    # 变化才重绘
                    self.changed.emit(frame)        # 变化才请求 OCR
            except Exception as exc:
                self.failed.emit(self.window_id, str(exc))
                self._sleep(0.5)                       # 出错降频重试
                continue

            elapsed = time.monotonic() - started
            interval = self._next_interval_ms(is_changed) / 1000.0
            self._sleep(max(0.0, interval - elapsed))
