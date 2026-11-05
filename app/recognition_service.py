# -*- coding: utf-8 -*-
"""★ 中心化识别服务：一份预热过的 OCR 模型 + 一条串行 OCR 线程，服务所有实时窗口。

设计要点：
  - 画面采集「每窗口一条轻量线程」，识别「全局一条线程」：
    多窗口也只加载一份模型，不会把 CPU 打满；
  - 待识别队列「每窗口只保留最新一帧」，过期帧直接丢弃，不产生积压；
  - OCR 线程启动即 warmup，用户框选时模型已就绪；
  - 结果带 window_id / frame_id 回派，窗口只负责画，不做任何计算。
"""
import time

from PySide6.QtCore import (
    QObject, QThread, Signal, Slot, QMutex, QWaitCondition, Qt,
)

from .box_refine import BoxRefiner
from .capture_worker import CaptureWorker, frame_to_image
from .ocr_engine import OcrEngine
from .rule_engine import CAMERA_CONTEXT, RuleMatcher, match_segments
from .segmenter import split_into_segments, segments_to_text


class _FrameQueue(QObject):
    """线程安全的「每窗口最新帧」队列。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._latest = {}
        self._order = []
        self._stopped = False

    @Slot(object)
    def push(self, frame):
        self._mutex.lock()
        wid = int(frame["window_id"])
        if wid not in self._order:
            self._order.append(wid)
        self._latest[wid] = frame       # 覆盖旧帧 = 丢弃过期帧
        self._cond.wakeOne()
        self._mutex.unlock()

    def pop(self, wait_ms=200):
        self._mutex.lock()
        if not self._order and not self._stopped:
            self._cond.wait(self._mutex, wait_ms)
        frame = None
        if self._order:
            wid = self._order.pop(0)
            frame = self._latest.pop(wid, None)
        stopped = self._stopped
        self._mutex.unlock()
        return frame, stopped

    def drop(self, window_id):
        wid = int(window_id)
        self._mutex.lock()
        self._latest.pop(wid, None)
        if wid in self._order:
            self._order.remove(wid)
        self._mutex.unlock()

    def stop(self):
        self._mutex.lock()
        self._stopped = True
        self._cond.wakeAll()
        self._mutex.unlock()

    def depth(self) -> int:
        """待识别窗口数，供控制面板显示积压情况。"""
        self._mutex.lock()
        try:
            return len(self._order)
        finally:
            self._mutex.unlock()


class _OcrThread(QThread):
    # wid, highlights, text, frame_id, ocr_line_count
    result_ready = Signal(int, object, object, int, int)
    ocr_error = Signal(int, str)
    # wid, done, total, stage(queued|running|done|failed)
    progress = Signal(int, int, int, str)

    def __init__(self, queue, engine, cfg, stats=None, parent=None):
        super().__init__(parent)
        self._queue = queue
        self.engine = engine
        self._stats = stats if stats is not None else {}
        self._matcher = RuleMatcher(
            cfg.get("rules", []), guard=bool(cfg["live"].get("context_guard", True))
        )
        self._punctuation = cfg["live"]["punctuation"]
        self._highlight_mode = str(cfg["live"].get("highlight_mode", "clause"))
        self._clause_max = int(cfg["live"].get("clause_max_chars", 60))
        self._min_interval_ms = int(cfg["live"]["ocr_min_interval_ms"])
        self._warmup = bool(cfg["live"]["warmup_on_start"])
        self._apply_ocr_cfg(cfg.get("ocr", {}))
        self._stopped = False

    def _apply_ocr_cfg(self, ocr_cfg):
        """识别精度相关开关（控制面板可改）。"""
        ocr_cfg = ocr_cfg or {}
        self._snap_boxes = bool(ocr_cfg.get("snap_boxes", True))
        self._detail_retry = bool(ocr_cfg.get("detail_retry", True))
        self._detail_max = max(0, int(ocr_cfg.get("detail_retry_max", 3) or 0))
        self._detail_upscale = float(ocr_cfg.get("detail_upscale", 2.0) or 2.0)

    def update_config(self, cfg):
        # 简单赋值在 GIL 下是原子的，规则热加载无需重启线程
        self._matcher = RuleMatcher(
            cfg.get("rules", []), guard=bool(cfg["live"].get("context_guard", True))
        )
        self._punctuation = cfg["live"]["punctuation"]
        self._highlight_mode = str(cfg["live"].get("highlight_mode", "clause"))
        self._clause_max = int(cfg["live"].get("clause_max_chars", 60))
        self._min_interval_ms = int(cfg["live"]["ocr_min_interval_ms"])
        self._apply_ocr_cfg(cfg.get("ocr", {}))

    def stop(self):
        self._stopped = True

    def _emit_progress(self, wid, done, total, stage):
        self.progress.emit(int(wid), int(done), int(total), str(stage))

    def _record_stats(self, started, line_count):
        """给控制面板用的轻量指标；dict 单键赋值在 GIL 下足够安全。"""
        if not self._stats:
            return
        ms = (time.monotonic() - started) * 1000.0
        self._stats["ocr_count"] = int(self._stats.get("ocr_count", 0)) + 1
        self._stats["last_ms"] = ms
        self._stats["total_ms"] = float(self._stats.get("total_ms", 0.0)) + ms
        self._stats["last_lines"] = int(line_count)
        self._stats["last_tiles"] = int(getattr(self.engine, "last_tiles", 0))
        self._stats["last_at"] = time.time()

    def _refine_misses(self, image, segments):
        """对「含镜头语境词却没命中任何规则」的句子做一次局部放大重认。

        OCR 对小字号 / 低对比度文字的识别错误（横移→横幅、环绕→环晓）
        是无法靠词表补救的，只能放大再看一遍。为了不拖慢整帧，
        限制次数（ocr.detail_retry_max），且只处理有语境线索的句子。
        """
        budget = self._detail_max
        out = []
        for seg in segments:
            if budget > 0 and self._needs_reread(seg):
                budget -= 1
                better = self._reread(image, seg)
                if better is not None:
                    seg = better
            out.append(seg)
        return out

    def _needs_reread(self, seg) -> bool:
        text = str(seg.get("text", ""))
        if len(text) < 2:
            return False
        if not any(word in text for word in CAMERA_CONTEXT):
            return False
        try:
            return not self._matcher.find_matches(text)
        except Exception:
            return False

    def _reread(self, image, seg):
        """裁剪该句 -> 放大重认 -> 取命中最多的一句；失败返回 None。"""
        box = seg.get("box") or []
        if len(box) < 4:
            return None
        try:
            x1, y1, x2, y2 = (float(v) for v in box[:4])
        except (TypeError, ValueError):
            return None
        pad = 10
        left = max(0, int(x1) - pad)
        top = max(0, int(y1) - pad)
        right = min(int(getattr(image, "width", 0)), int(x2) + pad)
        bottom = min(int(getattr(image, "height", 0)), int(y2) + pad)
        if right - left < 12 or bottom - top < 8:
            return None
        try:
            crop = image.crop((left, top, right, bottom))
            results = self.engine.ocr(crop, upscale=self._detail_upscale)
        except Exception:
            return None
        if not results:
            return None
        for item in results:
            item["box"] = [[float(p[0]) + left, float(p[1]) + top]
                           for p in item.get("box") or []]
        try:
            cand = split_into_segments(results, self._punctuation)
        except Exception:
            return None
        best, best_score = None, 0
        for seg2 in cand:
            hits = self._matcher.find_matches(seg2.get("text", ""))
            score = sum(end - start for start, end, _r, _m in hits)
            if score > best_score:
                best, best_score = seg2, score
        return best

    def run(self):
        if self._warmup:
            self.engine.warmup()          # 后台加载模型，不卡 UI

        last = 0.0
        while not self._stopped:
            frame, stopped = self._queue.pop(200)
            if stopped:
                break
            if frame is None:
                continue

            gap = self._min_interval_ms / 1000.0 - (time.monotonic() - last)
            while gap > 0 and not self._stopped:
                self.msleep(max(1, min(50, int(gap * 1000))))
                gap = self._min_interval_ms / 1000.0 - (time.monotonic() - last)
            if self._stopped:
                break

            wid = int(frame["window_id"])
            tiles = {"done": 0, "total": 1}
            started = time.monotonic()
            try:
                image = frame_to_image(frame)
                self._emit_progress(wid, 0, 0, "running")

                def _on_tile(done, total):
                    tiles["done"], tiles["total"] = done, total
                    self._emit_progress(wid, done, total, "running")

                results = self.engine.ocr(image, _on_tile)
                refiner = BoxRefiner(image, enabled=self._snap_boxes)
                segments = split_into_segments(results, self._punctuation, refiner)
                if self._detail_retry and self._detail_max and segments:
                    # 漏标的常见原因是小字被 det 认错，对「明显在讲镜头但没命中」
                    # 的句子做一次局部放大重认，代价可控（最多 N 句）
                    segments = self._refine_misses(image, segments)
                highlights = match_segments(
                    segments, self._matcher,
                    mode=self._highlight_mode, max_clause_chars=self._clause_max,
                )
                text = segments_to_text(segments)
                last = time.monotonic()
                self._record_stats(started, len(results))

                if not self.engine.available:
                    self._emit_progress(wid, 0, 0, "failed")
                    self.ocr_error.emit(wid, self.engine.error or "OCR 引擎不可用")
                else:
                    self._emit_progress(wid, tiles["total"], tiles["total"], "done")
                    # 即使 0 命中 / 0 行也要回派：窗口据此启动「空结果自动重试」
                    self.result_ready.emit(
                        wid, highlights, text, int(frame["frame_id"]),
                        len(results),
                    )
            except Exception as exc:
                self._emit_progress(wid, 0, 0, "failed")
                self.ocr_error.emit(wid, f"识别失败：{exc}")


class RecognitionService(QObject):
    result_ready = Signal(int, object, object, int, int)
    worker_error = Signal(int, str)
    progress_changed = Signal(int, int, int, str)   # wid, done, total, stage

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = OcrEngine(cfg["ocr"])
        self._stats = {
            "ocr_count": 0, "last_ms": 0.0, "total_ms": 0.0,
            "last_lines": 0, "last_tiles": 0, "last_at": 0.0,
        }
        self._queue = _FrameQueue(self)
        self._ocr = _OcrThread(self._queue, self.engine, cfg, self._stats, self)
        self._ocr.result_ready.connect(self.result_ready)
        self._ocr.ocr_error.connect(self.worker_error)
        self._ocr.progress.connect(self.progress_changed)
        self._captures = {}
        self._started = False

    def stats(self) -> dict:
        """当前运行指标（控制面板每秒轮询一次）。"""
        data = dict(self._stats)
        count = int(data.get("ocr_count", 0))
        data["avg_ms"] = (float(data.get("total_ms", 0.0)) / count) if count else 0.0
        data["queue"] = self._queue.depth()
        data["captures"] = len(self._captures)
        data["ready"] = self.engine._engine is not None
        data["attempted"] = bool(self.engine._tried)
        data["error"] = self.engine.error
        return data

    def start(self):
        if self._started:
            return
        self._started = True
        self._ocr.start()

    def add_window(self, window_id, rect) -> CaptureWorker:
        worker = CaptureWorker(window_id, rect, self.cfg["live"], self)
        worker.changed.connect(self._queue.push, Qt.QueuedConnection)
        worker.failed.connect(self.worker_error)
        self._captures[window_id] = worker
        worker.start()
        return worker

    def start_capture(self, window_id, rect) -> CaptureWorker:
        """切入 live 模式时按需启动采集线程；已有则直接复用。"""
        worker = self._captures.get(window_id)
        if worker is None:
            worker = self.add_window(window_id, rect)
        return worker

    def stop_capture(self, window_id):
        """切回 snapshot 模式时停掉采集线程，但保留识别队列。

        不调用 stop_window()：那会 drop 掉队列里尚未回派的识别结果，
        导致「切模式的一瞬间标注被清空」。
        """
        worker = self._captures.pop(window_id, None)
        if worker is not None:
            worker.stop()

    def enqueue(self, frame):
        """首帧 / 手动「立即重新识别」时直接投递。"""
        self._queue.push(frame)
        # 立刻告诉窗口「排上队了」，用户能感知点击有反馈
        try:
            self.progress_changed.emit(int(frame["window_id"]), 0, 0, "queued")
        except Exception:
            pass

    def pause_window(self, window_id, paused):
        worker = self._captures.get(window_id)
        if worker is not None:
            worker.set_paused(paused)

    def stop_window(self, window_id):
        worker = self._captures.pop(window_id, None)
        if worker is not None:
            worker.stop()
        self._queue.drop(window_id)

    def update_config(self, cfg):
        self.cfg = cfg
        self.engine.set_config(cfg["ocr"])
        self._ocr.update_config(cfg)
        live = cfg["live"]
        for worker in self._captures.values():
            worker.interval_ms = int(live["capture_interval_ms"])
            worker.threshold = float(live["frame_diff_threshold"])
            worker.step = max(1, int(live["diff_sample_step"]))
            worker.adaptive = bool(live.get("adaptive_capture", True))
            worker.idle_interval_ms = int(live.get("idle_capture_interval_ms", 400))
            min_changed = int(live.get("diff_min_changed_pixels", 6))
            worker.min_changed = min_changed if min_changed > 0 else 10 ** 9

    def shutdown(self):
        for wid in list(self._captures):
            self.stop_window(wid)
        self._ocr.stop()
        self._queue.stop()
        self._ocr.wait(3000)
