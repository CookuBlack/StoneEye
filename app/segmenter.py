# -*- coding: utf-8 -*-
"""★ 断句：把 OCR 的「整行」按标点切成短句，并支持在短句内按字符区间取子框。

两层定位：
  1) OCR 整行四点框 -> 按标点切成短句，每句有自己的子框（语义级）；
  2) 短句框 -> 规则命中的那几个字，按字符宽度插值得到精确小框（词级）。

这样既满足「按逗号/句号/分号断句」，又能让一句里同时出现的
「中景」「推镜」各自画一个框、各自用自己的颜色，而不是整句一个大框。

坐标估算：
  没有字符级 box 时，按「字符显示宽度权重」把横向区间按比例切分；
  中文/全角算 1.0，英文数字算 0.55，标点空格算 0.45。
  倾斜文本用四条边的线性插值，子框会跟着行的倾斜走。
"""
import re
import unicodedata
from functools import lru_cache

DEFAULT_PUNCTUATION = "，,。.；;、：:！!？?…~～|｜—–"

# OCR 经常在两个汉字之间插一个空格（"镜 头 缓慢 推 进"），
# 词表里没有带空格的写法，直接匹配必然漏标。
_CJK_GAP = re.compile(r"(?<=[\u3400-\u9fff])[ \t\u00a0]+(?=[\u3400-\u9fff])")


def squash_cjk_spaces(text: str) -> str:
    """去掉汉字之间的空白；英文单词之间的空格保留。"""
    if not text or " " not in text and "\t" not in text and "\u00a0" not in text:
        return text
    return _CJK_GAP.sub("", text)


@lru_cache(maxsize=4096)
def _eaw(ch: str) -> str:
    """字符的东亚宽度类别（按字符缓存，避免每帧重复查表）。"""
    return unicodedata.east_asian_width(ch)


@lru_cache(maxsize=4096)
def _is_wide(ch: str) -> bool:
    """字符是否为全角/宽字符。"""
    return _eaw(ch) in ("W", "F")


def _char_weight(ch: str, puncts) -> float:
    """字符的横向宽度权重（相对一个汉字）。

    中文标点（，。；）是**全角**，宽度与汉字相同；只有 ASCII 标点
    才是半角。以前统一按 0.45 算，会让整行在 10 个字之后累计漂移
    十几个像素（每经过一个标点就少算半格），这是「框画不准」的主因。
    """
    if ch.isspace():
        return 0.45
    eaw = _eaw(ch)
    if ch in puncts:
        if eaw in ("W", "F"):
            return 1.0
        return 0.9 if eaw == "A" else 0.45
    if eaw in ("W", "F"):
        return 1.0
    if eaw == "A":
        # — … · ～ 这类「模糊宽度」字符：中文排版里按接近全角算
        return 0.9
    return 0.55


def char_weights(text: str, punctuation=DEFAULT_PUNCTUATION):
    """返回 (weights, prefix, total)，prefix 长度为 len(text)+1。"""
    puncts = punctuation if isinstance(punctuation, (set, frozenset)) \
        else set(punctuation or DEFAULT_PUNCTUATION)
    weights = [_char_weight(ch, puncts) for ch in text]
    prefix = [0.0]
    for w in weights:
        prefix.append(prefix[-1] + w)
    return weights, prefix, prefix[-1]


def _spans(text: str, puncts: set):
    """返回 [(start, end, 去空白后的文本)]，标点本身不进入短句。"""
    result = []
    start = 0
    for i, ch in enumerate(text):
        if ch in puncts:
            if i > start:
                result.append((start, i))
            start = i + 1
    if start < len(text):
        result.append((start, len(text)))

    spans = []
    for start, end in result:
        raw = text[start:end]
        lead = len(raw) - len(raw.lstrip())
        trail = len(raw) - len(raw.rstrip())
        s, e = start + lead, end - trail
        if e > s:
            spans.append((s, e, text[s:e]))
    return spans


def _interp_box(box, t0: float, t1: float):
    """在文本框上取 [t0, t1] 横向比例对应的子矩形（支持倾斜、>=3 点）。"""
    pts = [(float(p[0]), float(p[1])) for p in box]
    if len(pts) < 3:
        return None
    ordered = sorted(pts, key=lambda p: p[0])
    left = ordered[:2]
    right = ordered[-2:]          # 用最后两点，3 点框也不会解包失败
    lt, lb = sorted(left, key=lambda p: p[1])
    rt, rb = sorted(right, key=lambda p: p[1])

    def at(t):
        top = (lt[0] + (rt[0] - lt[0]) * t, lt[1] + (rt[1] - lt[1]) * t)
        bot = (lb[0] + (rb[0] - lb[0]) * t, lb[1] + (rb[1] - lb[1]) * t)
        return top, bot

    a_top, a_bot = at(t0)
    b_top, b_bot = at(t1)
    return [
        min(a_top[0], a_bot[0]),
        min(a_top[1], b_top[1]),
        max(b_top[0], b_bot[0]),
        max(a_bot[1], b_bot[1]),
    ]


def sub_box(segment, start: int, end: int):
    """短句内 [start, end) 字符区间 -> 图像子框 [x1,y1,x2,y2]。

    用于「词级精确上色」：命中「俯拍镜头」就只框这 4 个字，
    不会把同一短句里的「穿插」也框进去。

    优先用 BoxRefiner 给出的**真实墨迹字符边界**（_xs），
    没有时再按字符宽度比例插值。
    """
    s = int(start)
    e = int(end)
    xs = segment.get("_xs")
    if xs and 0 <= s < e <= len(xs) - 1:
        y1, y2 = segment.get("_ys") or (0.0, 0.0)
        if not y2:
            box = segment.get("box") or [0, 0, 0, 0]
            y1, y2 = float(box[1]), float(box[3])
        return [float(xs[s]), float(y1), float(xs[e]), float(y2)]

    prefix = segment.get("_prefix")
    total = float(segment.get("_total") or 0.0)
    box = segment.get("box")
    if not prefix or total <= 0 or not box:
        return None
    s = max(0, min(len(prefix) - 1, s))
    e = max(s, min(len(prefix) - 1, e))
    if e <= s:
        return None
    t0, t1 = prefix[s] / total, prefix[e] / total

    # split_into_segments 已经把倾斜行压成轴对齐 [x1,y1,x2,y2]；
    # 短句内不换行，y 直接沿用，只按权重插值 x，比再走四点插值更准。
    if (len(box) == 4
            and all(not isinstance(p, (list, tuple)) for p in box)):
        x1, y1, x2, y2 = (float(v) for v in box)
        return [x1 + (x2 - x1) * t0, y1,
                x1 + (x2 - x1) * t1, y2]
    return _interp_box(box, t0, t1)


def split_into_segments(ocr_results, punctuation=DEFAULT_PUNCTUATION,
                        refiner=None):
    """[{text, box, score}] -> [{text, box(子框), score, line_index, _prefix, _total}]

    refiner: box_refine.BoxRefiner。给了就用真实墨迹边界定位，
    没有就退回按字符宽度插值（行为与旧版一致）。
    """
    puncts = set(punctuation or DEFAULT_PUNCTUATION)
    segments = []
    for line_index, item in enumerate(ocr_results):
        text = squash_cjk_spaces(str(item.get("text", "")))
        box = item.get("box") or []
        if not text.strip() or len(box) < 3:
            continue

        weights, prefix, total = char_weights(text, puncts)
        if total <= 0:
            continue

        geo = None
        if refiner is not None:
            try:
                geo = refiner.line(box, weights, prefix, total)
            except Exception:
                geo = None
            if geo is not None and len(geo.xs) != len(text) + 1:
                geo = None

        spans = _spans(text, puncts)
        if not spans:
            raw = text
            lead = len(raw) - len(raw.lstrip())
            trail = len(raw) - len(raw.rstrip())
            s, e = lead, len(raw) - trail
            spans = [(s, e, raw[s:e])] if e > s else []

        for start, end, seg_text in spans:
            if geo is not None:
                line_box = [float(geo.xs[start]), float(geo.y1),
                            float(geo.xs[end]), float(geo.y2)]
            else:
                t0 = prefix[start] / total
                t1 = prefix[end] / total
                if t1 - t0 <= 1e-6:
                    continue
                line_box = _interp_box(box, t0, t1)
            if line_box is None:
                continue

            # 短句自己的字符权重表，供 sub_box 做词级定位
            seg_weights = weights[start:end]
            seg_prefix = [0.0]
            for w in seg_weights:
                seg_prefix.append(seg_prefix[-1] + w)
            seg_total = seg_prefix[-1]

            segment = {
                "text": seg_text,
                "box": line_box,
                "score": float(item.get("score", 1.0)),
                "line_index": line_index,
                "_prefix": seg_prefix,
                "_total": seg_total,
            }
            if geo is not None:
                # 真实墨迹边界：整句框与词级下划线都贴到字上
                segment["_xs"] = geo.xs[start:end + 1]
                segment["_ys"] = (float(geo.y1), float(geo.y2))
            segments.append(segment)
    return segments


def segments_to_text(segments) -> str:
    """复制文本时一句一行，而不是 OCR 的一整行。"""
    return "\n".join(s["text"] for s in segments if s.get("text"))
