# -*- coding: utf-8 -*-
"""★ 文字框贴合：用像素墨迹校正 OCR 框的位置偏差。

OCR 返回的四点框有两个系统性误差：
  1. **左右留白**：检测框通常比字形宽一圈（尤其行首/行尾），
     按字符宽度均分时，越靠后偏差越大，最后一两个字能偏出半个字；
  2. **上下行距**：框高常把行间距算进来，框比字形高一截，看起来「框不贴着字」。

这里对每条水平文本行做一次灰度投影：
  - 收紧到真正有墨迹的行列范围（去留白）；
  - 按「空白列」把字符边界吸附到真实的字间缝隙上，
    拿不到缝隙的字（连笔、极小的字）退回按宽度均分。

倾斜超过阈值的行（竖排 / 斜排）直接跳过，仍用原来的四点插值，
因为按列投影在斜行上反而会算错。
"""
import math

import numpy as np


def _aabb(box):
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _is_level(box, tol=0.08) -> bool:
    """判断文本行是否接近水平（斜排不做列投影）。"""
    if len(box) < 4:
        return True
    pts = [(float(p[0]), float(p[1])) for p in box]
    ordered = sorted(pts, key=lambda p: p[0])
    left = sorted(ordered[:2], key=lambda p: p[1])
    right = sorted(ordered[-2:], key=lambda p: p[1])
    width = right[0][0] - left[0][0]
    if width <= 1:
        return False
    return abs(right[0][1] - left[0][1]) / width <= tol


class LineGeometry:
    """一条文本行的墨迹几何：收紧后的边框 + 每个字符边界所在的 x。"""

    __slots__ = ("x1", "y1", "x2", "y2", "xs")

    def __init__(self, x1, y1, x2, y2, xs):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.xs = xs            # 长度 = 字符数 + 1，绝对像素坐标

    def __repr__(self):                                  # pragma: no cover
        return (f"LineGeometry({self.x1:.0f},{self.y1:.0f},"
                f"{self.x2:.0f},{self.y2:.0f}, n={len(self.xs) - 1})")


class BoxRefiner:
    """把一张图的灰度缓存下来，按行反复取墨迹几何。"""

    def __init__(self, image=None, enabled=True):
        self.gray = None
        if not enabled or image is None:
            return
        try:
            self.gray = _to_gray(image)
        except Exception:
            self.gray = None

    def __bool__(self):
        return self.gray is not None

    def line(self, box, weights, prefix, total):
        """-> LineGeometry | None（None 表示这行不适用 / 数据不足）。"""
        if self.gray is None or total <= 0 or not box:
            return None
        if not _is_level(box):
            return None
        h, w = self.gray.shape[:2]
        x1, y1, x2, y2 = _aabb(box)
        ix1, iy1 = max(0, int(math.floor(x1))), max(0, int(math.floor(y1)))
        ix2, iy2 = min(w, int(math.ceil(x2))), min(h, int(math.ceil(y2)))
        if ix2 - ix1 < 4 or iy2 - iy1 < 4:
            return None

        region = self.gray[iy1:iy2, ix1:ix2]
        ink = _ink_mask(region)
        if ink is None:
            return None
        cols = ink.sum(axis=0)
        rows = ink.sum(axis=1)
        if not cols.any():
            return None

        # ---- 1) 收紧到真实墨迹范围（去掉 OCR 框的留白） ----
        nz = np.flatnonzero(cols > 0)
        tx1, tx2 = int(nz[0]), int(nz[-1]) + 1
        nzr = np.flatnonzero(rows > 0)
        ty1, ty2 = int(nzr[0]), int(nzr[-1]) + 1
        # 行高过小说明这一带几乎没字，缩放一下即可
        if tx2 - tx1 < 4 or ty2 - ty1 < 3:
            return None

        base_x = ix1 + tx1
        span = float(tx2 - tx1)
        base_y = iy1 + ty1
        height = float(ty2 - ty1)

        # ---- 2) 字符边界：先按宽度均分，再吸附到真实字间空隙 ----
        gaps = _gap_ranges(cols[tx1:tx2])
        unit = span / total
        xs = [base_x]
        for i in range(1, len(weights)):
            nominal = base_x + span * (prefix[i] / total)
            # 只在「本字符宽度」范围内搜索，避免相邻两个边界抢同一段空白
            local = unit * max(1.0, min(weights[i - 1], weights[i]))
            xs.append(_snap(nominal, gaps, base_x, span, local * 0.22))
        xs.append(base_x + span)
        # 吸附后必须严格递增；实在撞车就往后推半个像素，不让整行退回均分
        for i in range(1, len(xs)):
            xs[i] = min(base_x + span, max(xs[i], xs[i - 1] + 0.5))

        # 纵向取墨迹范围，上下各放 1px，避免压住笔画
        return LineGeometry(base_x - 1.0, base_y - 1.0,
                            base_x + span + 1.0, base_y + height + 1.0, xs)


def _to_gray(image):
    """PIL Image / numpy 数组 -> uint8 灰度 ndarray。"""
    if isinstance(image, np.ndarray):
        arr = image
        if arr.ndim == 3:
            arr = arr[:, :, :3].mean(axis=2)
        return arr.astype(np.uint8, copy=False)
    return np.asarray(image.convert("L"))


def _ink_mask(region):
    """返回墨迹掩码（True=字）。浅底深字和深底浅字都能处理。

    极性靠**框边缘**判断：OCR 框贴着字形，四条边绝大多数是背景色。
    不看「面积小的一侧」—— 粗体字 / 紧框里墨迹面积可能超过背景；
    也不只看列投影起伏 —— 框刚好卡住行高时两种极性的起伏很接近。
    边缘判断不明确时，再退回比较列投影的变异系数。
    """
    if region.size == 0:
        return None
    lo = float(np.percentile(region, 4))
    hi = float(np.percentile(region, 96))
    if hi - lo < 12:            # 对比度太低（纯色块）：没有可提取的字形
        return None
    dark = region < lo + 0.42 * (hi - lo)
    light = region > hi - 0.42 * (hi - lo)

    frame = np.concatenate([region[0, :], region[-1, :],
                            region[:, 0], region[:, -1]])
    bg = float(np.median(frame))
    mid = (lo + hi) / 2.0
    band = 0.06 * (hi - lo)
    if bg >= mid + band:
        return dark             # 背景亮 -> 字是暗的
    if bg <= mid - band:
        return light            # 背景暗 -> 字是亮的

    def spread(mask):
        cols = mask.sum(axis=0).astype(np.float32)
        mean = float(cols.mean())
        return -1.0 if mean <= 0 else float(cols.std()) / mean

    return dark if spread(dark) >= spread(light) else light


def _gap_ranges(cols):
    """连续空白列的区间 [(start, end)]，坐标相对给定的列数组。"""
    gaps = []
    run_start = None
    for i, v in enumerate(cols):
        if v <= 0:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                gaps.append((run_start, i - 1))
                run_start = None
    if run_start is not None:
        gaps.append((run_start, len(cols) - 1))
    return gaps


def _snap(x, gaps, low, span, limit):
    """把按宽度算出来的字符边界吸附到真实的字间空隙。

    找「离这个边界最近的一段空白」（距离按到区间算，不按中心算）：
      - 普通字缝（宽度 <= 12px）取中心，左右各留一半；
      - 很宽的空白（词间距 / 缩进）只取左缘 +3px，
        否则下划线会拖出很长一截；
      - limit 内没有空白就保持原值 —— 硬吸附会把字切成两半。
    """
    rel = x - low
    reach = max(0.5, float(limit))
    widest = max(2.0, float(limit) * 2.0)      # 超过这个宽度的空白多半在字形内部
    best, best_d = None, reach
    for start, end in gaps:
        if (end - start) > widest:
            continue
        distance = 0.0 if start <= rel <= end else min(abs(rel - start),
                                                      abs(rel - end))
        if distance <= best_d:
            best, best_d = (start, end), distance
    if best is None:
        return min(low + span, max(low, x))
    return min(low + span, max(low, low + (best[0] + best[1]) / 2.0))
