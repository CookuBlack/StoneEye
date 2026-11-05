# -*- coding: utf-8 -*-
"""屏幕截图（V3.4）：线程本地复用 mss 实例 + numpy 直出。

性能要点：
  1. 每次 mss.mss() 都会重建 DXGI / GDI 抓屏上下文（约 3~10ms）。
     live 模式每 100ms 抓一帧，这笔固定开销在整条链路里占比很高；
     改为「每线程缓存一个实例」，抓屏只剩真正的 blit。
  2. capture_rgb() 直接返回 RGB ndarray，采集线程用它同时完成
     帧差与 bytes 打包，省掉「PIL.Image -> convert -> tobytes」两次全图转换。
  3. 抓取失败（显示器热插拔 / DXGI 句柄失效）时丢弃实例重建一次再试。

坐标一律使用虚拟桌面的「物理像素」（支持多显示器下的负坐标）。
帧以 RGB bytes 跨线程传递，QPixmap 一律在主线程由 LiveWindow 构造，
并强制 devicePixelRatio=1 —— 若让 QPixmap 继承屏幕 DPR，
绘制时会被再缩放一次，高亮框就会整体偏移。
"""
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


_local = threading.local()


def _monitor(rect) -> dict:
    width, height = int(rect.w), int(rect.h)
    if width <= 0 or height <= 0:
        raise ValueError("选区尺寸无效")
    return {
        "left": int(rect.x),
        "top": int(rect.y),
        "width": width,
        "height": height,
    }


def _sct():
    """当前线程的 mss 实例（懒加载）。"""
    sct = getattr(_local, "sct", None)
    if sct is None:
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError(
                "缺少 mss，请执行 pip install -r requirements.txt"
            ) from exc
        sct = mss.mss()
        _local.sct = sct
    return sct


def release():
    """释放当前线程的抓屏上下文；采集线程退出前调用，避免句柄泄漏。"""
    sct = getattr(_local, "sct", None)
    _local.sct = None
    if sct is not None:
        try:
            sct.close()
        except Exception:
            pass


def grab(rect):
    """抓一帧，返回 mss 的 ScreenShot（含 bgra / size）。"""
    monitor = _monitor(rect)
    try:
        return _sct().grab(monitor)
    except Exception:
        # DXGI 句柄失效 / 显示器变化：丢掉旧实例重建一次
        release()
        return _sct().grab(monitor)


def capture(rect):
    """按虚拟桌面物理坐标截图，返回 PIL.Image (RGB)。"""
    shot = grab(rect)
    # PIL 只在这里按需导入，未安装时程序仍能启动并给出提示
    from PIL import Image
    return Image.frombytes("RGB", shot.size, shot.rgb)


def capture_rgb(rect):
    """返回 (h, w, 3) 的 uint8 RGB 数组；比 capture() 少两次全图转换。

    mss 的原始缓冲是 BGRA，取 [:, :, 2::-1] 即 RGB；
    负步长只是视图，tobytes()/差分运算都能正确处理，
    但为了让下游 strided 访问也保持高速，这里统一转成 C 连续。
    """
    import numpy as np

    shot = grab(rect)
    width, height = int(shot.width), int(shot.height)
    buf = np.frombuffer(shot.bgra, dtype=np.uint8, count=width * height * 4)
    view = buf.reshape(height, width, 4)[:, :, 2::-1]
    return np.ascontiguousarray(view)
