# -*- coding: utf-8 -*-
"""截长图的拼接内核：按重叠行找滚动位移，把新露出来的底部接上去。

V4.9.2 起**不再自动滚滚轮**：滚动完全由用户手动控制，抓取与拼接由
`long_capture.ManualLongCapture` 按固定间隔采样驱动。本模块只保留
纯计算部分（无 Qt、无 Win32），方便离屏自检直接喂合成帧验证。

算法：把两帧按「上一帧第 d 行起」和「下一帧第 0 行起」逐行比灰度差，
取误差最小的 d 就是滚动距离；新内容就是下一帧的最后 d 行。
列方向降采样到 1/4，一次匹配几百个候选位移也就几毫秒。
"""
import numpy as np


def best_shift(prev: np.ndarray, cur: np.ndarray):
    """返回 cur 相对 prev 向下滚动的像素数；找不到可靠重叠返回 None。"""
    h = min(prev.shape[0], cur.shape[0])
    if h < 80 or prev.ndim != 3 or cur.ndim != 3 \
            or prev.shape[1] != cur.shape[1]:
        return None
    a = prev[:, ::4].astype(np.int16)
    b = cur[:, ::4].astype(np.int16)
    # 手动滚轮一次可能滚得比自动滚轮多，候选范围放宽到 720 行。
    # 从 2 起、步长 2 粗搜：以前从 8 起会漏掉「慢慢滚 3~5px」的位移，
    # 而整行文字每隔一个行高长得几乎一样，算法会误选下一个行高，
    # 结果把重复内容拼进长图（真实事件循环回归里复现过）。
    limit = min(h - 40, 720)

    # 一阶段：便宜的粗搜，把所有可能的低分位移留下来。周期性排版
    # （等行高的文档 / 表格）会造出好几个几乎一样好的假位移，只看
    # 最小分数会挑错，所以不能在这里就定胜负。
    coarse = []
    for dy in range(2, limit, 2):
        length = min(h - dy, 220)
        if length < 24:
            break
        dif = float(np.abs(a[dy:dy + length] - b[:length]).mean())
        coarse.append((dif, dy))
    if not coarse:
        return None
    # 粗搜分数只是「相对排序」用的：短窗口 + 1/4 列降采样会把文字细节
    # 全抹平，真位移的绝对误差也常在 20~25。拿它当可靠性闸门会把
    # 每一次正常滚动都否掉，闸门必须放到下面的精搜结果上。
    coarse.sort()

    # 二阶段：在前 8 个候选附近，用更长的重叠区 + 翻倍列分辨率重打分。
    # 真位移在长窗口里会明显更贴合，整行重复的假解则会被拉开差距；
    # 顺便把奇数像素的位移也搜进来。
    a2 = prev[:, ::2].astype(np.int16)
    b2 = cur[:, ::2].astype(np.int16)
    best, best_dy = 1e9, None
    for _d, c in coarse[:8]:
        for dy in range(max(2, c - 3), min(limit, c + 4)):
            length = min(h - dy, 420)
            if length < 24:
                continue
            dif = float(np.abs(a2[dy:dy + length] - b2[:length]).mean())
            if dif < best:
                best, best_dy = dif, dy
    return best_dy if best < 22.0 else None


def stitch(frames):
    """frames: [RGB ndarray] -> 单张长图 ndarray（自检 / 离线拼接用）。"""
    if not frames:
        raise ValueError("没有可用帧")
    out = frames[0]
    for cur in frames[1:]:
        # 只拿上一帧高度的「底部」去比，长图顶部的旧内容不参与匹配
        tail = out[-cur.shape[0]:]
        dy = best_shift(tail, cur)
        if dy is None or dy < 2:
            break                    # 到底了 / 页面没动，别把重复内容接上去
        out = np.vstack((out, cur[cur.shape[0] - dy:]))
    return out
