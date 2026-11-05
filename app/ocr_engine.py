# -*- coding: utf-8 -*-
"""★ OCR 封装（V3.9）：保真识别 + 重叠分块 + 跨块同行合并 + 坐标还原 + 分块进度。

【V3.9 速度优化 —— 实测同一张 935x193 文本贴图 5.5s -> 0.5s】
真凶不是模型慢，是 RapidOCR 的默认检测策略：Det.limit_type=min + 736，
把「最小边」拉到 736px。一块 935x193 的矮长条会被放大成 3552x736 再推理，
像素量涨 15 倍，单次检测 50ms 变 1.5s。四处改动：
  1. det 改 limit_type=max（只缩不放）：块尺寸本来就由 tile_width 控制，
     检测按原像素跑，检测耗时 ~1.5s -> ~50ms，识别结果字符数还多 4 个；
  2. 关掉方向分类器 use_cls：屏幕截图都是正立的，这一趟纯浪费；
  3. rec 批大小 6 -> 1：onnxruntime 跑这种小模型，批处理反而慢 25%；
  4. 识别头输入高度自适应：行高 16~44px 时用 32（rec 输入 48x2304 -> 32x1536），
     实测快 3.6 倍且文字几乎一致；小字(<16px)和超大字(>44px)仍用 48 保精度。
  走的是「自己调 det/rec」的快路径，任何异常都自动退回原来的整调用，
  所以换 rapidocr 版本也不会崩。
另外加了「输入哈希 -> 结果」缓存：点重新识别、切规则重识别、双胞胎帧直接复用，
命中耗时 <2ms。

【为什么不用缩小整图】
  上一版把整块区域缩到 max_side=1280 再送检测。密集中文小字一旦被缩小，
  det 会整行漏检，于是「只识别出一处」。现在默认 **不缩小**，改为：
    - 超过 tile_width/height 的区域切重叠块，每块保持原始像素；
    - 相邻块在重叠区会把同一行识别两次，用「最长公共前后缀」拼回整行；
    - 所有 box 先加块偏移，再按整体缩放比例还原到原始截图坐标。

性能：静止画面由帧差跳过，不会跑 OCR；分块只在真正需要识别时执行。
进度：ocr(image, progress) 每完成一块回调 progress(done, total)，供 UI 画进度条。
性能：tile_max 限制单帧最多切几块（区域特别大时自动放大块尺寸），
      避免 4x4=16 块把一帧拖到好几秒；块尺寸越大越少但单块更慢，取折中。
"""
import threading
import time
import zlib


def _copy_item(item):
    """缓存命中时给调用方一份浅拷贝（box 也复制），避免下游改偏移污染缓存。"""
    out = dict(item)
    box = item.get("box")
    if box:
        out["box"] = [list(p) for p in box]
    return out


def _overlap_len(a: str, b: str) -> int:
    """a 的后缀与 b 的前缀的最长公共长度（>=2 才认为是同一行的重复段）。"""
    top = min(len(a), len(b))
    for k in range(top, 1, -1):
        if a[-k:] == b[:k]:
            return k
    return 0


def merge_tile_results(results):
    """合并分块带来的重复行 / 被边界切断的行。"""
    if not results:
        return []
    items = []
    for r in results:
        pts = r.get("box") or []
        if len(pts) < 3:
            continue
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        items.append({
            "text": str(r.get("text", "")),
            "score": float(r.get("score", 1.0)),
            "x1": min(xs), "x2": max(xs),
            "y1": min(ys), "y2": max(ys),
        })
    if not items:
        return []

    heights = sorted(max(1.0, i["y2"] - i["y1"]) for i in items)
    median_h = heights[len(heights) // 2]
    bucket = max(12.0, median_h * 0.9)
    # 同一行归入同一个桶，再按 x 排序处理
    items.sort(key=lambda i: (round(((i["y1"] + i["y2"]) / 2) / bucket), i["x1"]))

    merged = []
    for it in items:
        if not merged:
            merged.append(it)
            continue
        prev = merged[-1]
        same_bucket = (
            round(((prev["y1"] + prev["y2"]) / 2) / bucket)
            == round(((it["y1"] + it["y2"]) / 2) / bucket)
        )
        if not same_bucket:
            merged.append(it)
            continue

        gap = it["x1"] - prev["x2"]
        tolerance = max(24.0, 0.15 * max(1.0, prev["x2"] - prev["x1"]))

        # 重叠区完整重复：后者被前者包含
        if it["text"] and it["text"] in prev["text"] and gap <= tolerance:
            continue
        # 后者更完整：替换
        if prev["text"] and prev["text"] in it["text"] and gap <= tolerance:
            merged[-1] = it
            continue

        k = _overlap_len(prev["text"], it["text"])
        if k >= 2 and gap <= tolerance:
            prev["text"] += it["text"][k:]
            prev["x2"] = max(prev["x2"], it["x2"])
            prev["y1"] = min(prev["y1"], it["y1"])
            prev["y2"] = max(prev["y2"], it["y2"])
            prev["score"] = min(prev["score"], it["score"])
            continue
        merged.append(it)

    out = []
    for m in merged:
        out.append({
            "text": m["text"],
            "score": m["score"],
            "box": [
                [m["x1"], m["y1"]], [m["x2"], m["y1"]],
                [m["x2"], m["y2"]], [m["x1"], m["y2"]],
            ],
        })
    return out


def _axis_starts(length: int, size: int, overlap: int):
    """单个方向上的分块起点，最后一块贴边，保证覆盖完整。"""
    if length <= size:
        return [0]
    step = max(1, size - overlap)
    last = length - size
    starts = list(range(0, last + 1, step))
    if starts[-1] != last:
        starts.append(last)
    return starts


class OcrEngine:
    # 过窄/过矮的块补边到这个尺寸，避免 det 在极小的输入上崩。
    # 注意：RapidOCR 的 det 默认把「最小边」拉到 736（limit_type=min），
    # 一块 935x193 的矮长条会被放大成 3552x736 再推理 —— 像素量涨 15 倍，
    # 单次 det 从 50ms 变成 1.5s。这正是「识别慢」的主因，所以这里改用
    # limit_type=max（只缩不放），补边阈值也随之降到 64：矮块补边在
    # max 模式下不再能帮 det，只会白烧像素。
    MIN_TILE_SIDE = 64

    def __init__(self, cfg: dict | None = None):
        self._engine = None
        self._tried = False
        self._lock = threading.Lock()
        self.error = ""
        self.last_elapsed_ms = 0.0     # 供控制面板显示
        self.last_tiles = 0
        self.last_lines = 0
        self._cache = {}               # 输入哈希 -> 识别结果（重复帧直接复用）
        self._cache_order = []
        self.set_config(cfg or {})

    def set_config(self, cfg: dict):
        """配置热加载（不重建模型，避免重新加载几百 MB 权重）。"""
        self.min_confidence = float(cfg.get("min_confidence", 0.35))
        self.use_gpu = bool(cfg.get("use_gpu", False))
        self.max_side = int(cfg.get("max_side", 0) or 0)
        self.upscale = float(cfg.get("upscale", 1.0) or 1.0)
        self.tile_enabled = bool(cfg.get("tile_enabled", True))
        self.tile_width = int(cfg.get("tile_width", 960) or 960)
        self.tile_height = int(cfg.get("tile_height", 800) or 800)
        self.tile_overlap = int(cfg.get("tile_overlap", 160) or 0)
        self.tile_max = int(cfg.get("tile_max", 6) or 0)   # 0 = 不限制
        # 检测端缩放策略：max = 只缩不放（默认，快）；min = 老行为（放大，慢）
        self.det_limit_mode = str(cfg.get("det_limit_mode", "max") or "max").lower()
        if self.det_limit_mode not in ("max", "min"):
            self.det_limit_mode = "max"
        self.det_limit_side = int(cfg.get("det_limit_side", 1280) or 1280)
        # 屏幕截图是正立的，方向分类器（cls）纯属浪费时间；扫描件可打开
        self.use_angle_cls = bool(cfg.get("use_angle_cls", False))
        # rec 批大小：实测本机 batch=1 比 batch=6 快 25%（ORT 小模型批处理反而更慢）
        self.rec_batch = max(1, int(cfg.get("rec_batch", 1) or 1))
        # 识别头输入高度自适应：字号够大时用 32（rec 快 3 倍以上），
        # 小字仍走 48 保精度。见 _run_fast 里的分档逻辑。
        self.rec_height_auto = bool(cfg.get("rec_height_auto", True))
        self.rec_height_fast = int(cfg.get("rec_height_fast", 32) or 32)
        self._apply_live()

    def _apply_live(self):
        """把可热改的参数推给已加载的引擎（不重建 session）。"""
        engine = self._engine
        if engine is None:
            return
        try:
            engine.use_cls = self.use_angle_cls
        except Exception:
            pass
        try:
            engine.text_rec.rec_batch_num = self.rec_batch
        except Exception:
            pass
        try:
            engine.text_det.limit_type = self.det_limit_mode
            engine.text_det.limit_side_len = self.det_limit_side
        except Exception:
            pass

    def warmup(self) -> bool:
        """在后台线程调用，提前把模型加载好。"""
        self._ensure()
        return self._engine is not None

    @property
    def available(self) -> bool:
        self._ensure()
        return self._engine is not None

    def _ensure(self):
        with self._lock:
            if self._tried:
                return
            self._tried = True
            try:
                from rapidocr_onnxruntime import RapidOCR
                # use_gpu 由具体版本的 det_use_cuda 等参数控制，差异较大；
                # 统一用默认 CPU 初始化最稳妥，需要 GPU 时自行装 onnxruntime-gpu 并改这里。
                try:
                    self._engine = RapidOCR(
                        use_cls=self.use_angle_cls,
                        det_limit_type=self.det_limit_mode,
                        det_limit_side_len=self.det_limit_side,
                    )
                except TypeError:
                    # 老版本 rapidocr 不认这些关键字：退回默认构造，
                    # 由 _apply_live 尽量把参数塞进去（拿不到就维持原状）。
                    self._engine = RapidOCR()
                self._apply_live()
            except Exception as exc:
                self._engine = None
                self.error = f"RapidOCR 初始化失败（{type(exc).__name__}: {exc}）"

    def _rects_for(self, w: int, h: int, tw: int, th: int, ratio: float):
        """按给定块尺寸切网格；某方向只多出一点点时不切，避免重复块。"""
        ov = max(0, min(round(self.tile_overlap * ratio), tw - 1, th - 1))
        xs = _axis_starts(w, tw, ov) if w > tw + ov else [0]
        ys = _axis_starts(h, th, ov) if h > th + ov else [0]
        if len(xs) == 1 and len(ys) == 1:
            return [(0, 0, w, h)]
        return [
            (ox, oy, min(ox + tw, w), min(oy + th, h))
            for oy in ys for ox in xs
        ]

    def _tile_rects(self, w: int, h: int, ratio: float):
        if not self.tile_enabled:
            return [(0, 0, w, h)]
        tw = max(64, round(self.tile_width * ratio))
        th = max(64, round(self.tile_height * ratio))
        if w <= tw and h <= th:
            return [(0, 0, w, h)]

        rects = self._rects_for(w, h, tw, th, ratio)
        if self.tile_max <= 0:
            return rects
        # 块数超限时逐步放大块尺寸（单块更慢但总块数下降，整体更快）
        guard = 0
        while len(rects) > self.tile_max and guard < 8:
            tw = min(w, max(tw + 1, int(tw * 1.4)))
            th = min(h, max(th + 1, int(th * 1.4)))
            rects = self._rects_for(w, h, tw, th, ratio)
            guard += 1
        return rects

    @staticmethod
    def _pad_tile(image, min_w: int, min_h: int):
        """用背景色补齐太小的块，避免 OCR 内部把短边极端放大。"""
        w, h = image.size
        if w >= min_w and h >= min_h:
            return image
        from PIL import Image
        bg = image.getpixel((0, 0))
        canvas = Image.new("RGB", (max(w, min_w), max(h, min_h)), bg)
        canvas.paste(image, (0, 0))
        return canvas

    def ocr(self, image, progress=None, upscale=None):
        """PIL.Image -> [{text, box(原图坐标), score}]，仅在 OCR 工作线程调用。

        progress(done, total)：每识别完一个分块回调一次，供 UI 显示进度；
        total=1 时代表整图一次识别（无分块）。回调异常一律吞掉，不能影响识别。
        upscale：单次调用临时覆盖放大倍数（局部二次识别用，不受配置影响）。
        """
        self._ensure()
        if self._engine is None:
            return []

        def _tick(done, total):
            if progress is None:
                return
            try:
                progress(int(done), int(total))
            except Exception:
                pass

        try:
            from PIL import Image
            import numpy as np

            work = image.convert("RGB")
            orig_w, orig_h = work.size

            ratio = 1.0
            want = float(upscale if upscale else self.upscale)
            if want > 1.0001:
                ratio = want
            elif self.max_side > 0 and max(orig_w, orig_h) > self.max_side:
                ratio = self.max_side / max(orig_w, orig_h)

            # 同一张图被重复要求识别（点「重新识别」、切规则重识别、
            # 帧差没拦住的双胞胎帧）时直接复用上次结果，连模型都不用进。
            key = None
            try:
                key = (zlib.crc32(work.tobytes()), orig_w, orig_h, round(ratio, 3))
                hit = self._cache.get(key)
                if hit is not None:
                    self.last_elapsed_ms = 0.0
                    self.last_tiles = hit[1]
                    self.last_lines = len(hit[0])
                    _tick(hit[1], hit[1])
                    return [_copy_item(it) for it in hit[0]]
            except Exception:
                key = None

            if abs(ratio - 1.0) > 1e-6:
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:          # 兼容较旧 Pillow
                    resample = Image.LANCZOS
                work = work.resize(
                    (max(1, round(orig_w * ratio)), max(1, round(orig_h * ratio))),
                    resample,
                )
            ww, wh = work.size
            sx, sy = orig_w / ww, orig_h / wh       # 还原到原始截图的系数

            # 块可以补到比整图更大：补的是纯色空白，坐标原点不变，
            # 目的是让 OCR 的 det 输入短边稳定在 320 左右。
            min_w = min_h = self.MIN_TILE_SIDE
            rects = self._tile_rects(ww, wh, ratio)
            results = []
            last_exc = None
            total = len(rects)
            _tick(0, total)
            started = time.monotonic()

            for index, (ox, oy, ex, ey) in enumerate(rects, 1):
                crop = work.crop((ox, oy, ex, ey))
                crop = self._pad_tile(crop, min_w, min_h)
                try:
                    block = self._run_engine(np.asarray(crop))
                except Exception as exc:       # 单块失败不影响其它块
                    last_exc = exc
                    _tick(index, total)
                    continue
                for item in block:
                    item["box"] = [
                        [(float(p[0]) + ox) * sx, (float(p[1]) + oy) * sy]
                        for p in item["box"]
                    ]
                results.extend(block)
                _tick(index, total)

            if not results and last_exc is not None:
                self.error = f"OCR 运行失败（{type(last_exc).__name__}: {last_exc}）"
                self.last_elapsed_ms = (time.monotonic() - started) * 1000.0
                self.last_tiles = total
                self.last_lines = 0
                return []
            out = merge_tile_results(results) if total > 1 else results
            self.last_elapsed_ms = (time.monotonic() - started) * 1000.0
            self.last_tiles = total
            self.last_lines = len(out)
            if key is not None:
                self._remember(key, out, total)
            return out
        except Exception as exc:
            self.error = f"OCR 运行失败（{type(exc).__name__}: {exc}）"
            return []

    def _run_engine(self, arr):
        """跑一次识别。优先走快路径，任何异常都退回底层整调用。

        快路径 = 检测一次 + 按行高分档做识别头，省掉「方向分类」这一整趟，
        并让大字号文本用更矮的识别头（rec 输入 48→32，快 3 倍；小字仍用 48）。
        极端比例（超宽扁条 / 极小图）仍交给原实现，它会补 letterbox。
        """
        try:
            out = self._run_fast(arr)
            if out is not None:
                return out
        except Exception:
            pass
        raw, _ = self._engine(arr)
        return self._normalize(raw)

    def _run_fast(self, arr):
        """返回 None 表示「这条路由不了，请用原实现」。"""
        engine = self._engine
        h, w = int(arr.shape[0]), int(arr.shape[1])
        if min(h, w) < 24 or w / max(1, h) > 6.0:
            return None
        boxes, _ = engine.text_det(arr)
        if boxes is None:
            return []
        if len(boxes) == 0:
            return []
        boxes = engine.sorted_boxes(boxes)
        crops = engine.get_crop_img_list(arr, boxes)
        if not crops:
            return []

        try:
            engine.text_rec.rec_batch_num = self.rec_batch
        except Exception:
            pass
        if self.rec_height_auto:
            hs = sorted(int(c.shape[0]) for c in crops)
            median_h = hs[len(hs) // 2]
            # 只在「行高 16~44px」这一段用 32 高的识别头：
            #   <16px 是小字，压缩会掉字，必须走 48；
            #   >44px 是超大字号，32 等于把字压掉一半，也掉字。
            target = self.rec_height_fast if 16 <= median_h <= 44 else 48
            try:
                engine.text_rec.rec_image_shape = [3, int(target), 320]
            except Exception:
                pass

        rec_res, _ = engine.text_rec(crops)
        out = []
        for box, pair in zip(boxes, rec_res):
            try:
                text, score = pair[0], float(pair[1])
            except (TypeError, IndexError, ValueError):
                continue
            if not text or score < self.min_confidence:
                continue
            pts = (box.tolist() if hasattr(box, "tolist")
                   else [[float(p[0]), float(p[1])] for p in box])
            pts = [[float(p[0]), float(p[1])] for p in pts]
            if len(pts) < 3:
                continue
            out.append({"text": str(text), "box": pts, "score": score})
        return out

    def _remember(self, key, out, tiles):
        """只保留最近 4 份（一张图 + 几次局部重认的裁剪图），内存占用可忽略。"""
        try:
            self._cache[key] = (out, tiles)
            self._cache_order.append(key)
            while len(self._cache_order) > 4:
                old = self._cache_order.pop(0)
                self._cache.pop(old, None)
        except Exception:
            pass

    def _normalize(self, raw):
        """兼容不同 rapidocr 版本的返回结构。"""
        results = []
        if not raw:
            return results
        for item in raw:
            try:
                if isinstance(item, dict):
                    box = item.get("box")
                    text = item.get("text", "")
                    score = item.get("score", item.get("confidence", 1.0))
                elif isinstance(item, (list, tuple)) and len(item) >= 3:
                    box, text, score = item[0], item[1], item[2]
                else:
                    continue
                score = float(score)
                if score < self.min_confidence:
                    continue
                points = [[float(pt[0]), float(pt[1])] for pt in box]
                if len(points) < 3:
                    continue
                text = str(text)
                if not text.strip():
                    continue
                results.append({"text": text, "box": points, "score": score})
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        return results
