# -*- coding: utf-8 -*-
"""识别规则页（控制面板 -> 侧栏「识别规则」）。

V4.3 起不再弹独立窗口：整个编辑器就是面板里的一页，跟「显示 / 高级参数」
平级。原来的 QDialog 会盖住面板、还要再点一次关闭，用户反馈「别另外开窗口」。

用户不用再手动改 config.json：左侧类别、右侧关键词与颜色，
下方可以粘一句话即时看命中结果，确认词表对不对再保存。
保存后立刻用新词表重新识别所有贴图（不必重启程序）。

字段与 config.json 的 rules 一一对应：
    name / category / type(keyword|regex) / patterns / color / fill /
    fill_color / line_width / enabled
"""
from copy import deepcopy
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QLineEdit, QPlainTextEdit,
    QListWidget, QListWidgetItem, QCheckBox, QFrame, QVBoxLayout,
    QHBoxLayout, QColorDialog, QMessageBox,
)

from .coords import ui_zoom
from .rule_engine import RuleMatcher
from .ui_theme import px as px_of
from .ui_widgets import AnimatedButton, card as make_card

_SPLIT_HINT = "每行一个词，也可用空格 / 逗号分隔"
_DEFAULT_NEW_RULE = {
    "name": "新类别", "category": "新类别", "type": "keyword",
    "patterns": [], "color": "#34C759", "line_width": 3,
    "fill": True, "fill_color": "#34C75940", "enabled": True,
}


def split_patterns(text: str):
    """关键词文本 -> 词表；支持换行 / 空格 / 逗号 / 顿号 / 分号。"""
    raw = str(text or "")
    for ch in ("，", "、", ";", "；", "\n", "\r", "\t", ",", " "):
        raw = raw.replace(ch, "\x00")
    return [p.strip() for p in raw.split("\x00") if p.strip()]


def _color_text(value) -> str:
    """把任意写法收敛成 #RRGGBB，避免存进配置后 QColor 解析失败。"""
    color = QColor(str(value or "").strip())
    if not color.isValid():
        return "#34C759"
    return color.name().upper()


class RulesPage(QWidget):
    """嵌在控制面板里的一页，不是窗口 —— 没有 exec()，也不会挡住面板。"""

    saved = Signal()          # 保存成功（面板据此刷新侧栏计数 / 状态条）

    def __init__(self, controller, scale=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._scale = float(scale or ui_zoom())
        self._rules = []
        self._baseline = []
        self._index = -1
        self._loading = False

        # 面板内容区很窄，编辑器自己不再留外边距
        self.setMinimumHeight(self._px(430))
        self._build()
        self.reload()

    # ---------- 与配置同步 ----------
    def reload(self):
        """丢掉未保存的改动，从配置重新读一遍。"""
        self._rules = deepcopy(self.controller.cfg.get("rules", []) or [])
        self._baseline = deepcopy(self._rules)
        self._reload_list(0)
        self._set_saved_hint("")

    def refresh_if_clean(self):
        """配置被外部改了（重载配置 / 托盘改词表）时才跟着刷新，别吃掉正在编辑的内容。"""
        if not self.is_dirty():
            self.reload()

    def is_dirty(self) -> bool:
        self._commit_current()
        return self._rules != self._baseline

    # ---------- 构建 ----------
    def _px(self, value):
        return px_of(value, self._scale)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(self._px(10))

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(self._px(10))

        # 左：类别列表
        left = QVBoxLayout()
        left.setSpacing(self._px(6))
        self._list = QListWidget()
        self._list.setMinimumWidth(self._px(210))
        self._list.currentRowChanged.connect(self._on_pick)
        left.addWidget(self._list, 1)
        tools = QHBoxLayout()
        tools.setSpacing(self._px(6))
        self._btn_add = QPushButton("＋ 新增")
        self._btn_del = QPushButton("－ 删除")
        self._btn_default = QPushButton("恢复默认")
        for btn in (self._btn_add, self._btn_del, self._btn_default):
            btn.setObjectName("mini")
            btn.setCursor(Qt.PointingHandCursor)
            tools.addWidget(btn)
        self._btn_add.clicked.connect(self._on_add)
        self._btn_del.clicked.connect(self._on_delete)
        self._btn_default.clicked.connect(self._on_restore)
        left.addLayout(tools)
        body.addLayout(left, 0)

        # 右：类别详情（用面板同一套 Card，圆角和顶部高光才对得上）
        panel_card, right = make_card(self._scale, 8)

        row = QHBoxLayout()
        row.setSpacing(self._px(8))
        name_label = QLabel("名称")
        name_label.setObjectName("field")
        name_label.setFixedWidth(self._px(40))
        self._name = QLineEdit()
        self._name.setPlaceholderText("景别 / 运镜 / 光线 …")
        self._name.textChanged.connect(self._on_name_changed)
        self._enabled = QCheckBox("启用")
        row.addWidget(name_label)
        row.addWidget(self._name, 1)
        row.addWidget(self._enabled)
        right.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(self._px(8))
        color_label = QLabel("颜色")
        color_label.setObjectName("field")
        color_label.setFixedWidth(self._px(40))
        self._color = QLineEdit()
        self._color.setPlaceholderText("#FF3B30")
        self._swatch = QLabel()
        self._swatch.setFixedSize(self._px(22), self._px(22))
        self._btn_color = QPushButton("取色…")
        self._btn_color.setObjectName("mini")
        self._btn_color.setCursor(Qt.PointingHandCursor)
        self._btn_color.clicked.connect(self._pick_color)
        self._color.editingFinished.connect(self._on_color_text)
        row2.addWidget(color_label)
        row2.addWidget(self._swatch)
        row2.addWidget(self._color, 1)
        row2.addWidget(self._btn_color)
        right.addLayout(row2)

        kw_label = QLabel(f"关键词（{_SPLIT_HINT}）")
        kw_label.setObjectName("field")
        right.addWidget(kw_label)
        self._words = QPlainTextEdit()
        self._words.setPlaceholderText("大特写\n特写\n中景\n全景")
        self._words.textChanged.connect(self._on_words_changed)
        right.addWidget(self._words, 1)

        preview_head = QLabel("试一句")
        preview_head.setObjectName("field")
        right.addWidget(preview_head)
        self._preview = QLineEdit()
        self._preview.setPlaceholderText(
            "粘一句分镜描述，立刻看命中哪些词（例如：镜头缓慢向前推进，画面切全景）"
        )
        self._preview.textChanged.connect(self._run_preview)
        right.addWidget(self._preview)
        self._preview_result = QLabel("输入一句话试试，命中结果会显示在这里")
        self._preview_result.setObjectName("stat")
        self._preview_result.setWordWrap(True)
        right.addWidget(self._preview_result)

        body.addWidget(panel_card, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setSpacing(self._px(8))
        self._count = QLabel("")
        self._count.setObjectName("stat")
        footer.addWidget(self._count)
        self._saved_hint = QLabel("")
        self._saved_hint.setObjectName("stat")
        footer.addWidget(self._saved_hint)
        footer.addStretch(1)
        # 页面内不需要「取消/关闭」，改成「放弃修改」：丢掉未保存的改动，
        # 重新从 config.json 读回来
        self._btn_reset = QPushButton("放弃修改")
        self._btn_reset.setCursor(Qt.PointingHandCursor)
        self._btn_reset.setMinimumHeight(self._px(35))
        self._btn_reset.setToolTip("丢掉这次编辑，重新从 config.json 读取")
        self._btn_reset.clicked.connect(self._on_reset_clicked)
        self._btn_save = AnimatedButton("保存并重新识别", self._scale,
                                        kind="primary")
        self._btn_save.setFixedHeight(self._px(35))
        self._btn_save.clicked.connect(self._on_save)
        footer.addWidget(self._btn_reset)
        footer.addWidget(self._btn_save)
        root.addLayout(footer)

    # ---------- 列表 ----------
    def _reload_list(self, select=0):
        self._loading = True
        try:
            self._list.clear()
            for rule in self._rules:
                item = QListWidgetItem(self._row_text(rule))
                color = QColor(str(rule.get("color") or "#888888"))
                if color.isValid():
                    item.setForeground(color)
                self._list.addItem(item)
            if self._rules:
                idx = max(0, min(select, len(self._rules) - 1))
                self._list.setCurrentRow(idx)
                self._index = idx
                self._show(idx)
            else:
                self._index = -1
                self._show(-1)
            self._update_count()
        finally:
            self._loading = False

    @staticmethod
    def _row_text(rule) -> str:
        name = str(rule.get("name") or rule.get("category") or "未命名")
        count = len(rule.get("patterns") or [])
        flag = "" if rule.get("enabled", True) else "（已停用）"
        return f"{name}{flag}   {count} 词"

    def _update_count(self):
        words = sum(len(r.get("patterns") or []) for r in self._rules)
        self._count.setText(f"{len(self._rules)} 类 · {words} 个关键词")

    def _on_pick(self, index):
        if self._loading:
            return
        self._commit_current()
        self._index = index
        self._show(index)

    def _commit_current(self):
        """把右侧控件写回当前规则（切换类别 / 保存前调用）。"""
        if not 0 <= self._index < len(self._rules):
            return
        rule = self._rules[self._index]
        name = self._name.text().strip() or "未命名"
        rule["name"] = name
        rule["category"] = name
        rule["enabled"] = bool(self._enabled.isChecked())
        rule["color"] = _color_text(self._color.text())
        rule["fill_color"] = rule["color"] + "40"
        rule["patterns"] = split_patterns(self._words.toPlainText())
        self._list.item(self._index).setText(self._row_text(rule))
        self._update_count()

    def _show(self, index):
        enabled = 0 <= index < len(self._rules)
        for widget in (self._name, self._color, self._words,
                       self._enabled, self._btn_color):
            widget.setEnabled(enabled)
        if not enabled:
            self._name.setText("")
            self._color.setText("")
            self._words.setPlainText("")
            self._enabled.setChecked(False)
            self._set_swatch("#333333")
            self._preview_result.setText("先新增一个类别")
            return
        rule = self._rules[index]
        self._loading = True
        try:
            self._name.setText(str(rule.get("name") or ""))
            self._color.setText(str(rule.get("color") or "#34C759"))
            self._words.setPlainText("\n".join(rule.get("patterns") or []))
            # 内容比可视区高时，Qt 会把光标放到末尾顺带滚下去，
            # 结果第一行被切掉半个字；这里强制回到顶部
            self._words.verticalScrollBar().setValue(0)
            self._enabled.setChecked(bool(rule.get("enabled", True)))
            self._set_swatch(str(rule.get("color") or "#34C759"))
        finally:
            self._loading = False
        self._run_preview()

    def _set_swatch(self, color_text):
        color = QColor(str(color_text))
        if not color.isValid():
            color = QColor("#333333")
        self._swatch.setStyleSheet(
            f"background: {color.name()}; border-radius: 6px;"
            " border: 1px solid #343434;"
        )

    # ---------- 编辑 ----------
    def _on_name_changed(self, text):
        if self._loading:
            return
        if 0 <= self._index < len(self._rules):
            self._rules[self._index]["name"] = text.strip() or "未命名"
            self._list.item(self._index).setText(self._row_text(self._rules[self._index]))

    def _on_color_text(self):
        if self._loading:
            return
        text = _color_text(self._color.text())
        self._loading = True
        try:
            self._color.setText(text)
        finally:
            self._loading = False
        self._set_swatch(text)
        self._run_preview()

    def _pick_color(self):
        current = QColor(str(self._color.text()))
        color = QColorDialog.getColor(
            current if current.isValid() else QColor("#34C759"),
            self, "选择标注颜色",
        )
        if color.isValid():
            self._color.setText(color.name().upper())
            self._on_color_text()

    def _on_words_changed(self):
        if self._loading:
            return
        self._run_preview()

    def _on_add(self):
        self._commit_current()
        rule = deepcopy(_DEFAULT_NEW_RULE)
        used = {str(r.get("name")) for r in self._rules}
        n = 2
        while rule["name"] in used:
            rule["name"] = f"新类别{n}"
            rule["category"] = rule["name"]
            n += 1
        self._rules.append(rule)
        self._reload_list(len(self._rules) - 1)
        self._name.setFocus()
        self._name.selectAll()

    def _on_delete(self):
        if not 0 <= self._index < len(self._rules):
            return
        name = str(self._rules[self._index].get("name"))
        if QMessageBox.question(
            self, "删除类别", f"删除「{name}」及其全部关键词？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self._rules.pop(self._index)
        self._reload_list(min(self._index, len(self._rules) - 1))

    def _on_restore(self):
        if QMessageBox.question(
            self, "恢复默认",
            "用内置默认词表覆盖当前编辑内容？（未保存前不会写盘）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        from .config_manager import ConfigManager
        self._rules = deepcopy(ConfigManager.defaults().get("rules", []))
        self._reload_list(0)

    # ---------- 预览 ----------
    def _run_preview(self):
        text = self._preview.text().strip()
        if not text:
            self._preview_result.setText("输入一句话试试，命中结果会显示在这里")
            return
        rules = deepcopy(self._rules)
        if 0 <= self._index < len(self._rules):
            name = self._name.text().strip() or "未命名"
            rules[self._index]["patterns"] = split_patterns(self._words.toPlainText())
            rules[self._index]["name"] = name
            rules[self._index]["category"] = name
        guard = bool(self.controller.cfg.get("live", {}).get("context_guard", True))
        try:
            matcher = RuleMatcher(rules, guard=guard)
            hits = matcher.find_matches(text)
        except Exception as exc:
            self._preview_result.setText(f"规则有误：{exc}")
            return
        if not hits:
            self._preview_result.setText("没有命中。检查关键词是否写全，或这一段本来不含运镜描述。")
            return
        parts = []
        for _s, _e, rule, matched in hits:
            category = str(rule.get("category") or rule.get("name") or "其他")
            parts.append(f"{category}「{matched}」")
        blocked = getattr(matcher, "suppressed", 0)
        note = f"（另有 {blocked} 处被语义校验拦下）" if blocked else ""
        self._preview_result.setText("命中：" + "、".join(parts) + note)

    # ---------- 保存 ----------
    def _on_save(self):
        self._commit_current()
        rules = []
        for rule in self._rules:
            patterns = [p for p in (rule.get("patterns") or []) if str(p).strip()]
            if not patterns:
                continue                      # 空类别不写入，避免状态条挂个 0
            rule = dict(rule)
            rule["patterns"] = patterns
            rule["color"] = _color_text(rule.get("color"))
            rule["fill_color"] = rule["color"] + "40"
            rule["enabled"] = bool(rule.get("enabled", True))
            rules.append(rule)
        if not rules:
            QMessageBox.warning(self, "没有可保存的规则",
                               "至少要有一个类别且包含关键词。")
            return
        try:
            self.controller.apply_panel_patch({"rules": rules}, reocr=True)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        # 保存后以「落盘后的规则」为新的基准，否则页面会一直处于"有未保存改动"
        self._rules = deepcopy(rules)
        self._baseline = deepcopy(rules)
        self._reload_list(self._index)
        self._set_saved_hint(f"● 已保存并重新识别　{datetime.now():%H:%M}")
        self.saved.emit()

    def _on_reset_clicked(self):
        if self.is_dirty() and QMessageBox.question(
            self, "放弃修改", "丢掉这次编辑，重新读取 config.json？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.reload()

    def _set_saved_hint(self, text: str):
        self._saved_hint.setText(text)
