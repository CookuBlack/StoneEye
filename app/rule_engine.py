# -*- coding: utf-8 -*-
"""★ 规则匹配（V3.3）：以「逗号/句号/分号切出来的短句」为标注单位。

两档粒度，由 live.highlight_mode 决定：

  clause（默认）—— 短句里只要有命中，就把**整句**框起来上色。
      这才是「按标点断句」的字面含义：框的左右边界严格贴着标点，
      用户看到的是一整段分镜描述（如「镜头缓慢向前推进」整句），
      而不是句中的两个字。同一句里同时出现景别和运镜时，
      第二类用内缩 3px 的框画在里面，保证两种颜色都看得见。
      超过 clause_max_chars 的长句自动退化为词级，避免一个框横跨半屏。

  word —— 只框命中的那几个字（旧模式，便于精确看词）。

重叠处理：同一区间只保留「最长」的命中，避免「中近景」被拆成
「中景」+「近景」两个框；长度相同时按配置顺序（景别优先于运镜）。
"""
import re
from functools import lru_cache

from PySide6.QtGui import QColor

from .segmenter import sub_box


def parse_qcolor(value: str, fallback: str = "#FF3B30") -> QColor:
    """解析 #RGB / #RRGGBB / #RRGGBBAA，8 位按 RRGGBBAA 解释。

    不直接用 QColor(str)，不同 Qt 版本对 8 位十六进制的 alpha 位置解释不一致。
    """
    text = str(value or fallback).strip().lstrip("#")
    try:
        if len(text) == 3:
            r, g, b = (int(c * 2, 16) for c in text)
            a = 255
        elif len(text) == 6:
            r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
            a = 255
        elif len(text) == 8:
            r, g, b, a = (int(text[i:i + 2], 16) for i in (0, 2, 4, 6))
        else:
            return QColor(fallback)
        return QColor(r, g, b, a)
    except (ValueError, TypeError):
        return QColor(fallback)


@lru_cache(maxsize=256)
def _parse_cached(value: str, fallback: str) -> QColor:
    return parse_qcolor(value, fallback)


def cached_qcolor(value, fallback: str = "#FF3B30") -> QColor:
    """带缓存的 QColor 解析，返回**副本**。

    贴图每条框每次重绘都要解析一次颜色串，进度条 60ms 一跳时这点开销
    会被放大；缓存后只剩一次字典查找。返回副本是因为调用方会 setAlpha，
    直接返回缓存对象会污染后续所有同类颜色的透明度。
    """
    return QColor(_parse_cached(str(value or fallback), str(fallback)))


def _normalize(text) -> str:
    return re.sub(r"\s+", "", str(text)).casefold()


# ---- 语境校验：把「物体自己在动」和「镜头在动」分开 -------------------
# 这些词单独出现时歧义很大：「环绕」既可能是运镜，也可能是凳子环绕草坪。
AMBIGUOUS_VERBS = frozenset((
    "环绕", "旋转", "平移", "跟随", "升降", "手持", "推进", "前推",
    "后拉", "推近", "拉远", "横移", "纵移", "左移", "右移", "上移",
    "下移", "推拉",
))
# 句子里出现任意一个，就认定整句在讲镜头语言，裸动词直接采纳
CAMERA_CONTEXT = (
    "镜头", "机位", "摄影机", "摄像机", "相机", "运镜", "画面", "视点",
    "视角", "拍摄", "取景", "构图", "景别", "特写", "全景", "远景",
    "近景", "中景", "跟拍", "航拍", "摇臂", "焦距", "推拉摇移",
)
# 裸动词左边紧挨着这些字，说明主语是物体而不是镜头
OBJECT_CHARS = frozenset(
    "凳桌椅柜墙柱树木花草叶车船门窗桥塔钟旗帜球衣裙帽猫狗鸟鱼山梯"
    "人男女孩子群队伍灯屏杯瓶伞刀枪"
)
# 「推进情绪 / 推进项目」这类抽象宾语，不是镜头运动
ABSTRACT_OBJECTS = (
    "情绪", "情节", "剧情", "故事", "叙事", "氛围", "气氛", "节奏",
    "工作", "项目", "计划", "改革", "建设", "进程", "发展", "合作",
    "谈判", "关系", "业务", "事业", "教学", "研究", "更新",
)
_LEFT_FILLERS = re.compile(r"[的得地着过了在是从向沿把被和与及]")


def _context_ok(text: str, start: int, end: int, matched: str) -> bool:
    """裸动词是否需要语境佐证。返回 False 表示判定为物体动作，不标注。

    「橙色环形坐凳环绕草坪」里的「环绕」是长凳围着草坪，不是镜头环绕；
    「轻微环绕空间」里的「环绕」才是运镜。判别依据：
      1) 整句有镜头/画面类语境词 -> 一定是镜头语言；
      2) 动词左边 8 个字里有实体名词 -> 物体动作，抑制；
      3) 动词后紧跟抽象宾语（推进情绪）/ 手持器物 -> 抑制。
    """
    if matched not in AMBIGUOUS_VERBS:
        return True
    if any(word in text for word in CAMERA_CONTEXT):
        return True
    left = _LEFT_FILLERS.sub("", text[max(0, start - 8):start])
    if any(ch in OBJECT_CHARS for ch in left):
        return False
    right = text[end:end + 2]
    if right and any(right.startswith(word) for word in ABSTRACT_OBJECTS):
        return False
    if matched in ("手持", "环绕", "旋转") and right:
        if any(ch in OBJECT_CHARS for ch in right):
            return False
    return True


def _keyword_regex(patterns):
    """把整张词表合成一条正则：一次 finditer 就能拿到所有命中位置。

    词按长度降序排列，保证 re 的交替不会让「中景」抢先截断「中近景」；
    纯英文词加字母边界，避免 PAN 命中 PANDA。
    """
    words = sorted((str(p) for p in patterns if str(p)),
                   key=lambda p: (-len(p), p))
    if not words:
        return None
    parts = []
    for p in words:
        escaped = re.escape(p)
        bare = p.replace(" ", "")
        if p.isascii() and bare.isalpha():
            parts.append(r"(?<![A-Za-z])" + escaped + r"(?![A-Za-z])")
        else:
            parts.append(escaped)
    try:
        return re.compile("(?:" + "|".join(parts) + ")", re.IGNORECASE)
    except re.error:
        return None


class RuleMatcher:
    """编译一次，之后对任意短句做全量片段匹配。"""

    def __init__(self, rules, guard: bool = True):
        self.rules = list(rules or [])
        self._compiled = []       # [(priority, rule, [(regex, raw), ...])]
        self.guard = bool(guard)
        self.suppressed = 0       # 上次匹配被语境校验否掉的条数（调试用）
        self._build()

    def _build(self):
        compiled = []
        for priority, rule in enumerate(self.rules):
            # 控制面板 / 规则编辑器可以临时停用一类，不必删词
            if rule.get("enabled") is False:
                continue
            is_regex = str(rule.get("type", "keyword")).lower() == "regex"
            raws = [str(p) for p in rule.get("patterns", []) if str(p)]
            entries = []
            if is_regex:
                for raw in raws:
                    try:
                        entries.append((re.compile(raw, re.IGNORECASE), raw))
                    except re.error:
                        continue          # 非法正则跳过，不让程序崩
            else:
                rx = _keyword_regex(raws)
                if rx is not None:
                    entries.append((rx, "<keywords>"))
            if entries:
                compiled.append((priority, rule, entries))
        self._compiled = compiled

    def find_matches(self, text: str):
        """-> [(start, end, rule, matched_text)]，已去除重叠、按位置有序。"""
        text = str(text)
        candidates = []
        blocked = 0
        for priority, rule, entries in self._compiled:
            for rx, _raw in entries:
                for m in rx.finditer(text):
                    s, e = m.span()
                    if e <= s:
                        continue
                    matched = m.group(0)
                    if self.guard and not _context_ok(text, s, e, matched):
                        blocked += 1
                        continue
                    candidates.append((s, e, e - s, priority, rule, matched))
        self.suppressed = blocked

        # 起点靠前 -> 命中更长 -> 规则在配置里更靠前
        candidates.sort(key=lambda c: (c[0], -c[2], c[3]))
        accepted = []
        for s, e, _length, _priority, rule, matched in candidates:
            if any(s < a_end and e > a_start
                   for a_start, a_end, _r, _m in accepted):
                continue
            accepted.append((s, e, rule, matched))
        return accepted

    def match(self, text):
        """兼容旧接口：返回第一条命中规则（内部仍走全量匹配）。"""
        hits = self.find_matches(text)
        return hits[0][2] if hits else None


def _group_spans(hits):
    """把命中片段按「类别+颜色」聚合，保证同一句里同类词只出一个框。"""
    groups = []
    index = {}
    for start, end, rule, matched in hits:
        category = str(rule.get("category") or rule.get("name") or "其他")
        key = (category, str(rule.get("color", "")))
        if key not in index:
            index[key] = len(groups)
            groups.append({"rule": rule, "spans": [], "words": []})
        group = groups[index[key]]
        group["spans"].append((start, end))
        if matched not in group["words"]:
            group["words"].append(matched)
    return groups


def match_segments(segments, matcher: RuleMatcher, padding: float = 2.0,
                   mode: str = "clause", max_clause_chars: int = 60):
    """短句 -> 高亮框列表。

    mode="clause"：整句上色，框边界就是标点边界。
        一个短句每类只出一个框；两类同句时第二类内缩 3px 叠在里面。
    mode="word"：只框命中的词（每个词一个框）。
    """
    clause_mode = str(mode or "clause").strip().lower() != "word"
    limit = max(1, int(max_clause_chars or 60))
    highlights = []

    for seg in segments:
        text = str(seg.get("text", ""))
        if not text.strip():
            continue
        hits = matcher.find_matches(text)
        if not hits:
            continue

        groups = _group_spans(hits)
        box = seg.get("box") or []
        use_clause = (clause_mode and len(text) <= limit
                      and len(box) >= 4)

        if use_clause:
            x1, y1, x2, y2 = (float(v) for v in box[:4])
            for order, group in enumerate(groups):
                inset = order * 3.0     # 同类重叠时内缩，两种颜色都能看见
                hx1, hy1 = x1 - padding + inset, y1 - padding + inset
                hx2, hy2 = x2 + padding - inset, y2 + padding - inset
                if hx2 - hx1 < 4 or hy2 - hy1 < 4:
                    hx1, hy1, hx2, hy2 = x1, y1, x2, y2
                # 命中词在图上的精确位置：整句框只圈范围，这几个字加实线下划线，
                # 避免「镜头从广场铺地人视高度开始平稳前推」整句都被当运镜
                word_boxes = []
                for start, end in group["spans"]:
                    sub = sub_box(seg, start, end)
                    if sub is not None:
                        word_boxes.append([round(v, 1) for v in sub])
                highlights.append({
                    "box": [hx1, hy1, hx2, hy2],
                    "rule": group["rule"],
                    "text": " ".join(group["words"]),
                    "segment": text,
                    "scope": "clause",
                    "word_boxes": word_boxes,
                })
            continue

        for group in groups:
            for start, end in group["spans"]:
                sub = sub_box(seg, start, end)
                if sub is None:
                    continue
                sx1, sy1, sx2, sy2 = sub
                highlights.append({
                    "box": [sx1 - padding, sy1 - padding,
                            sx2 + padding, sy2 + padding],
                    "rule": group["rule"],
                    "text": text[start:end],
                    "segment": text,
                    "scope": "word",
                })
    return highlights


def summarize(highlights, rules=None):
    """按 category 统计命中数，供状态栏显示「景别 N · 运镜 M」。"""
    counts = {}
    colors = {}
    for rule in rules or []:
        colors[str(rule.get("category") or rule.get("name") or "其他")] = str(
            rule.get("color", "#FFFFFF"))
    for item in highlights or []:
        rule = item.get("rule") or {}
        key = str(rule.get("category") or rule.get("name") or "其他")
        counts[key] = counts.get(key, 0) + 1
        if rule.get("color"):
            colors[key] = str(rule["color"])
    return counts, colors
