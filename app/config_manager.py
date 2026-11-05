# -*- coding: utf-8 -*-
"""配置读写：默认配置、深合并、容错校验（V3.2 快照模式）。

打包成 exe 后配置文件放在 exe 同目录，方便直接用记事本修改。
完整词表以 config.json 为准；这里的 DEFAULT_CONFIG 只是配置缺失时的兜底。
"""
import json
import os
import sys
from copy import deepcopy


DEFAULT_RULES = [
    {
        "name": "景别", "category": "景别", "type": "keyword",
        "patterns": [
            "大特写", "特写", "中近景", "近景", "中景", "中全景", "全景",
            "大远景", "远景", "过肩", "主观视角", "POV", "空镜", "侧拍视角",
            "俯拍视角", "鸟瞰", "半身", "全身", "群像",
        ],
        "color": "#FF3B30", "line_width": 3,
        "fill": True, "fill_color": "#FF3B3040",
    },
    {
        "name": "运镜", "category": "运镜", "type": "keyword",
        "patterns": [
            "镜头缓慢向前推进", "镜头缓慢拉远", "镜头前推", "镜头推近",
            "推镜", "拉镜", "摇镜", "移镜", "跟镜", "跟拍",
            "前推", "推进", "推近", "后拉", "拉远", "横移", "纵移",
            "环绕空间", "环绕镜头", "环绕", "环拍", "环视",
            "俯拍镜头", "俯拍", "仰拍", "升降镜头", "升降", "变焦",
            "手持镜头", "手持", "斯坦尼康", "运镜平稳", "一镜到底",
        ],
        "color": "#0A84FF", "line_width": 3,
        "fill": True, "fill_color": "#0A84FF40",
    },
]

DEFAULT_CONFIG = {
    "hotkey": "ctrl+alt+q",
    "ocr": {
        "use_gpu": False,
        "min_confidence": 0.35,
        # 0 = 不缩小（保真，密集小字识别更全）；设 1600 之类可换取速度
        "max_side": 0,
        "upscale": 1.0,             # >1 时先放大再识别，小字更准但更慢
        "tile_enabled": True,        # 大区域切成重叠块，保持原始分辨率
        "tile_width": 960,
        "tile_height": 800,
        "tile_overlap": 160,
        "tile_max": 6,               # 单帧最多切几块，超出自动放大块尺寸
        # 检测端缩放策略。RapidOCR 默认 limit_type=min + 736：把「最小边」
        # 拉到 736，一块 935x193 的矮长条会被放大成 3552x736 再推理，
        # 像素量涨 15 倍，单次检测从 50ms 变 1.5s —— 这是识别慢的主因。
        # max = 只缩不放（默认）：块尺寸已由 tile_width 控制，检测按原像素跑。
        # 觉得小字漏检可改回 "min"（慢），或把 upscale 设成 1.5。
        "det_limit_mode": "max",
        "det_limit_side": 1280,      # max 模式下的最长边上限，块比它大才缩
        "use_angle_cls": False,      # 屏幕截图都是正立的，方向分类纯属浪费时间
        "rec_batch": 1,              # 实测小模型批处理反而更慢，1 最快
        # 识别头输入高度自适应：行高 16~44px 时用 32（rec 快 3 倍），
        # 更小的字或超大字仍用标准 48，避免压缩掉笔画
        "rec_height_auto": True,
        "rec_height_fast": 32,
        # 用像素墨迹收紧 OCR 框、吸附字符边界（框更贴字，标点位置更准）
        "snap_boxes": True,
        # 漏标兜底：句子明显在讲镜头却没命中时，局部放大再认一次
        "detail_retry": True,
        "detail_retry_max": 3,       # 单帧最多重认几句，限制延迟
        "detail_upscale": 2.0,
    },
    "live": {
        # snapshot = 框选瞬间冻结成静态贴图（切屏不会改变内容，默认）
        # live     = 每 100ms 重新抓同一区域（需要监控区域变化时才开）
        "capture_mode": "snapshot",
        "status_corner": "bottom_right",   # 状态条位置，默认右下角不挡正文
        # clause = 以逗号/句号/分号为界，命中的「整句」上色（默认，边框和标点严格对齐）
        # word   = 只框住命中的词本身（旧模式）
        "highlight_mode": "clause",
        "clause_max_chars": 60,   # 超长句退化为词级，避免一个框横跨半屏
        "show_progress": True,    # 顶部细进度条 + 状态条百分比
        "progress_hold_ms": 900,  # 识别完成后停留显示「已识别 x.xs」
        "capture_interval_ms": 100,
        "ocr_min_interval_ms": 300,
        "frame_diff_threshold": 1.0,
        "diff_sample_step": 4,
        "diff_min_changed_pixels": 6,   # 局部改一个字也能触发重识别
        # 画面连续静止时自动降频抓屏（省 CPU），有变化立刻恢复正常频率
        "adaptive_capture": True,
        "idle_capture_interval_ms": 400,
        "warmup_on_start": True,
        "show_status": True,
        "show_legend": True,
        # 语境校验：裸运镜动词（环绕/推进/手持…）若无镜头语境且左侧是
        # 实体主语，就判定为物体自身动作，不标注（避免「坐凳环绕草坪」）
        "context_guard": True,
        "empty_retry_interval_ms": 1200,   # 识别为空时自动重试（模型预热/漏检）
        "empty_retry_limit": 5,
        "punctuation": "，,。.；;、：:！!？?…~～|｜—–",
    },
    "sticker": {
        "default_zoom": 1.0,
        "min_zoom": 0.2,
        "max_zoom": 5.0,
        "zoom_step": 0.1,
        "default_opacity": 1.0,
        "show_hint_on_create": True,
        "place_at_source": True,   # 快照模式直接贴在源区域原位（不再需要错开）
        "source_offset": 24,       # live 模式 / place_at_source=false 时的错位距离
        # 原位粘贴时画面和背景长得一样，必须画一圈边框才知道「这里被截下来了」
        "show_border": True,
        "border_color": "#00E5FF",
        # 出现时闪一下（描边高亮 + 轻微淡入），让人一眼看到截下来的区域
        "flash_on_create": True,
    },
    "rules": DEFAULT_RULES,
    # 控制面板自己的界面状态（不属于识别参数，改坏了也不影响主流程）
    "ui": {
        "panel_geometry": None,   # [x, y, w, h]，None = 首次打开居中
        "advanced_open": False,   # 「高级参数」分组是否展开
    },
}


def app_dir() -> str:
    """项目根目录（开发环境）或 exe 所在目录（PyInstaller 打包后）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path() -> str:
    return os.path.join(app_dir(), "config.json")


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def _clamp(container: dict, key: str, default: float,
           low: float, high: float) -> float:
    try:
        value = float(container.get(key, default))
    except (TypeError, ValueError):
        value = default
    if value != value:  # NaN
        value = default
    value = max(low, min(high, value))
    container[key] = value
    return value


def _as_int(container: dict, key: str, default: int,
           low: int, high: int) -> int:
    value = int(_clamp(container, key, default, low, high))
    container[key] = value
    return value


def _sanitize(cfg: dict) -> dict:
    """把用户可能写坏的配置修正到合法范围，避免运行时崩溃。"""
    hotkey = cfg.get("hotkey")
    if not isinstance(hotkey, str) or not hotkey.strip():
        cfg["hotkey"] = DEFAULT_CONFIG["hotkey"]

    ocr = cfg.setdefault("ocr", {})
    if not isinstance(ocr, dict):
        ocr = cfg["ocr"] = {}
    _clamp(ocr, "min_confidence", 0.35, 0.0, 1.0)
    ocr["use_gpu"] = bool(ocr.get("use_gpu", False))
    max_side = int(_clamp(ocr, "max_side", 0, 0, 8192))
    ocr["max_side"] = 0 if max_side < 320 else max_side
    _clamp(ocr, "upscale", 1.0, 0.5, 3.0)
    ocr["tile_enabled"] = bool(ocr.get("tile_enabled", True))
    _as_int(ocr, "tile_width", 960, 320, 4096)
    _as_int(ocr, "tile_height", 800, 320, 4096)
    _as_int(ocr, "tile_overlap", 160, 0, 600)
    _as_int(ocr, "tile_max", 6, 0, 64)
    mode = str(ocr.get("det_limit_mode", "max")).strip().lower()
    ocr["det_limit_mode"] = mode if mode in ("max", "min") else "max"
    _as_int(ocr, "det_limit_side", 1280, 320, 4096)
    ocr["use_angle_cls"] = bool(ocr.get("use_angle_cls", False))
    _as_int(ocr, "rec_batch", 1, 1, 16)
    ocr["rec_height_auto"] = bool(ocr.get("rec_height_auto", True))
    _as_int(ocr, "rec_height_fast", 32, 16, 64)
    ocr["snap_boxes"] = bool(ocr.get("snap_boxes", True))
    ocr["detail_retry"] = bool(ocr.get("detail_retry", True))
    _as_int(ocr, "detail_retry_max", 3, 0, 20)
    _clamp(ocr, "detail_upscale", 2.0, 1.0, 4.0)
    if ocr["tile_overlap"] >= ocr["tile_width"]:
        ocr["tile_overlap"] = max(0, ocr["tile_width"] // 4)

    live = cfg.setdefault("live", {})
    if not isinstance(live, dict):
        live = cfg["live"] = {}
    mode = str(live.get("capture_mode", "snapshot")).strip().lower()
    live["capture_mode"] = "live" if mode in ("live", "realtime", "real_time") else "snapshot"
    corner = str(live.get("status_corner", "bottom_right")).strip().lower()
    if corner not in ("bottom_right", "bottom_left", "top_right", "top_left"):
        corner = "bottom_right"
    live["status_corner"] = corner
    hmode = str(live.get("highlight_mode", "clause")).strip().lower()
    live["highlight_mode"] = "word" if hmode in ("word", "keyword", "词", "词级") else "clause"
    _as_int(live, "clause_max_chars", 60, 8, 200)
    live["show_progress"] = bool(live.get("show_progress", True))
    _as_int(live, "progress_hold_ms", 900, 0, 5000)
    _as_int(live, "capture_interval_ms", 100, 33, 2000)
    _as_int(live, "ocr_min_interval_ms", 300, 0, 5000)
    _clamp(live, "frame_diff_threshold", 1.0, 0.0, 50.0)
    _as_int(live, "diff_sample_step", 4, 1, 16)
    _as_int(live, "diff_min_changed_pixels", 6, 0, 100000)
    live["adaptive_capture"] = bool(live.get("adaptive_capture", True))
    _as_int(live, "idle_capture_interval_ms", 400, 33, 3000)
    live["warmup_on_start"] = bool(live.get("warmup_on_start", True))
    live["show_status"] = bool(live.get("show_status", True))
    live["show_legend"] = bool(live.get("show_legend", True))
    live.pop("smart_select", None)   # V3.8 已移除窗口自动预测，清掉旧配置残留
    live["context_guard"] = bool(live.get("context_guard", True))
    _as_int(live, "empty_retry_interval_ms", 1200, 200, 10000)
    _as_int(live, "empty_retry_limit", 5, 0, 30)
    punct = live.get("punctuation")
    live["punctuation"] = punct if isinstance(punct, str) else DEFAULT_CONFIG["live"]["punctuation"]

    st = cfg.setdefault("sticker", {})
    if not isinstance(st, dict):
        st = cfg["sticker"] = {}
    _clamp(st, "min_zoom", 0.2, 0.05, 1.0)
    _clamp(st, "max_zoom", 5.0, 1.0, 20.0)
    if st["max_zoom"] < st["min_zoom"]:
        st["max_zoom"] = st["min_zoom"]
    _clamp(st, "default_zoom", 1.0, st["min_zoom"], st["max_zoom"])
    _clamp(st, "zoom_step", 0.1, 0.01, 1.0)
    _clamp(st, "default_opacity", 1.0, 0.1, 1.0)
    st["show_hint_on_create"] = bool(st.get("show_hint_on_create", True))
    st["place_at_source"] = bool(st.get("place_at_source", True))
    _as_int(st, "source_offset", 24, 0, 400)
    st["show_border"] = bool(st.get("show_border", True))
    st["flash_on_create"] = bool(st.get("flash_on_create", True))
    bc = str(st.get("border_color") or "#00E5FF")
    st["border_color"] = bc if bc.startswith("#") else "#" + bc

    raw_rules = cfg.get("rules")
    if not isinstance(raw_rules, list):
        raw_rules = []
    rules = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        r = dict(item)
        r["name"] = str(r.get("name") or "未命名规则")
        # category 用于状态栏按「景别 / 运镜」两大类聚合，同色规则可共用一类
        r["category"] = str(r.get("category") or r["name"])
        r["type"] = "regex" if str(r.get("type", "keyword")).lower() == "regex" else "keyword"
        patterns = r.get("patterns")
        r["patterns"] = [str(p) for p in patterns] if isinstance(patterns, (list, tuple)) else []
        r["color"] = str(r.get("color") or "#FF3B30")
        fc = str(r.get("fill_color") or (r["color"].lstrip("#") + "40"))
        r["fill_color"] = fc if fc.startswith("#") else "#" + fc
        r["fill"] = bool(r.get("fill", False))
        r["enabled"] = bool(r.get("enabled", True))
        _clamp(r, "line_width", 3, 0.5, 20.0)
        rules.append(r)
    cfg["rules"] = rules

    ui = cfg.setdefault("ui", {})
    if not isinstance(ui, dict):
        ui = cfg["ui"] = {}
    geo = ui.get("panel_geometry")
    clean = None
    if isinstance(geo, (list, tuple)) and len(geo) == 4:
        try:
            clean = [int(v) for v in geo]
        except (TypeError, ValueError):
            clean = None
    # 尺寸太小的记录没有意义（窗口被拖到只剩一条缝），直接丢掉重新居中
    if clean is not None and (clean[2] < 300 or clean[3] < 300):
        clean = None
    ui["panel_geometry"] = clean
    ui["advanced_open"] = bool(ui.get("advanced_open", False))
    return cfg


class ConfigManager:
    def __init__(self, path: str | None = None):
        self.path = path or config_path()

    @staticmethod
    def defaults() -> dict:
        """返回一份经过校验的默认配置，调用方可直接安全使用。"""
        return _sanitize(deepcopy(DEFAULT_CONFIG))

    def ensure_exists(self) -> None:
        if not os.path.isfile(self.path):
            self.write(self.defaults())

    def load(self) -> dict:
        data = {}
        if os.path.isfile(self.path):
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config.json 顶层必须是一个 JSON 对象 { ... }")
        return _sanitize(_deep_merge(self.defaults(), data))

    def write(self, cfg: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
