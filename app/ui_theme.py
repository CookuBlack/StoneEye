# -*- coding: utf-8 -*-
"""控制面板的视觉规范（V4.4 · 中性灰阶 + 统一的圆角 / 字重 / 字体层级）。

配色沿用截图采样出来的中性灰，不引入彩色强调：贴图上那抹分类色
（运镜蓝 / 景别橙）才是画面里唯一有颜色的东西，面板不跟它抢。

V4.4 主要做三件事：

  1. **圆角分级** —— 之前 7/8/9/10/12/13 六种半径混用，看着"哪里都不太对"。
     现在收敛成四级：窗口 16 / 卡片 14 / 控件 9 / 小件 8。
  2. **字重与字体** —— 标题、数值、正文、辅助文字各用各的：
     中文 Microsoft YaHei UI，数字走等宽（Cascadia Mono → Consolas），
     标题 600 加粗，正文 400，辅助文字降亮度不提字号。
  3. **投影式层级** —— 卡片比内容区亮一档，顶部再压一条极淡的高光，
     控件叠在卡片上；靠明度分层，不靠边框和分割线堆砌。
"""
import re

# ---- 圆角（逻辑像素，随 DPI 缩放）----
R_WIN = 18      # 整窗（V4.5 从 16 提到 18，配投影才看得出"抬起来"）
R_CARD = 14     # 卡片
R_CTRL = 9      # 按钮 / 输入框 / 分段控件
R_SM = 8        # 导航项 / 小标签

# ---- 色板（改这里就够了）----
BG_ROOT = "#151515"      # 内容区
BG_TOP = "#191919"       # 内容区顶部（整窗一层极淡的自上而下渐变）
BG_BOTTOM = "#121212"    # 内容区底部
BG_SIDE = "#1C1C1C"      # 侧栏
BG_CARD = "#1B1B1B"      # 卡片
BG_CARD_HI = "#1E1E1E"   # 卡片顶部高光
BG_RAISED = "#242424"    # 悬浮控件 / 胶囊按钮
BG_TRACK = "#1E1E1E"     # 分段轨道 / 内嵌槽
BD_CARD = "#2A2A2A"      # 卡片描边
BD_SOFT = "#3B3B3B"      # 胶囊按钮描边
BD_STRONG = "#4C4C4C"    # 输入框描边
SEL_BG = "#2F2F2F"       # 侧栏选中
SEL_BG_HOVER = "#252525"  # 侧栏悬停
SEG_ON = "#484848"       # 分段选中
TEXT = "#EFEFEF"
TEXT_2 = "#A0A0A0"
TEXT_3 = "#6F6F6F"
ACCENT = "#EFEFEF"       # 白色强调

# 字体栈：QSS 里写一串，Qt 用第一个装了的
FONT_UI = '"Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", sans-serif'
FONT_NUM = '"Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace'

PANEL_QSS = """
QWidget#root { font-family: %(FONT_UI)s;
               font-size: 13px;
               color: %(TEXT)s; }
QDialog#root { background: %(BG_ROOT)s; }

QLabel { color: %(TEXT)s; font-size: 13px;
         font-family: %(FONT_UI)s;
         background: transparent; }

/* ---- 文字层级：大标题 > 卡片标题 > 正文 > 辅助 ---- */
QLabel#hero    { font-size: 25px; font-weight: 600; color: #F7F7F7; }
QLabel#herosub { font-size: 12px; color: %(TEXT_3)s; }
QLabel#pagetitle { font-size: 15px; font-weight: 600; color: #F2F2F2; }
QLabel#caption { font-size: 11px; font-weight: 600; color: %(TEXT_2)s;
                 letter-spacing: 0.8px; }
QLabel#field   { color: %(TEXT_2)s; font-size: 13px; }
QLabel#value   { color: %(TEXT)s; font-size: 13px; font-weight: 600;
                 font-family: %(FONT_NUM)s; }
QLabel#stat    { color: %(TEXT_3)s; font-size: 12px; }
QLabel#hint    { color: %(TEXT_3)s; font-size: 12px; }
QLabel#swlabel { color: #DEDEDE; font-size: 13px; font-weight: 500; }
QLabel#swhint  { color: %(TEXT_3)s; font-size: 11px; }
QLabel#sectitle { color: %(TEXT_3)s; font-size: 11px; font-weight: 600;
                  letter-spacing: 0.9px; }
QLabel#brand   { color: #F4F4F4; font-size: 15px; font-weight: 600; }
QLabel#brandver { color: %(TEXT_3)s; font-size: 11px;
                  font-family: %(FONT_NUM)s; }
QLabel#metric  { color: #F6F6F6; font-size: 24px; font-weight: 500;
                 font-family: %(FONT_NUM)s; }
QLabel#metriclabel { color: %(TEXT_3)s; font-size: 11px; }
QLabel#navvalue { color: %(TEXT_2)s; font-size: 11px;
                  font-family: %(FONT_NUM)s; }
QLabel#kbd { background: #202020; color: #DADADA;
             border: 1px solid #303030; border-radius: 6px;
             padding: 3px 7px; font-size: 10px; font-family: %(FONT_NUM)s; }

/* ---- 容器 ---- */
QFrame#card  { background: %(BG_CARD)s; border: 1px solid %(BD_CARD)s;
               border-radius: %(R_CARD)spx; }
QFrame#well  { background: %(BG_TRACK)s; border: 1px solid %(BD_CARD)s;
               border-radius: 10px; }
QFrame#divider { background: #272727; border: none; max-height: 1px; }

QWidget#swrow { background: transparent; border-radius: %(R_SM)spx; }
QWidget#swrow:hover { background: #232323; }

/* ---- 按钮 ---- */
QPushButton { background: %(BG_RAISED)s; color: #E6E6E6; border: none;
              border-radius: %(R_CTRL)spx; padding: 8px 14px;
              font-size: 13px; font-family: %(FONT_UI)s; }
QPushButton:hover   { background: #2E2E2E; }
QPushButton:pressed { background: #1D1D1D; }
QPushButton:disabled { color: #565656; background: #1D1D1D; }

QPushButton[primary="true"] { background: #EFEFEF; color: #121212;
                              font-weight: 600; }
QPushButton[primary="true"]:hover { background: #FFFFFF; }
QPushButton[primary="true"]:pressed { background: #D6D6D6; }
QPushButton[primary="true"]:disabled { background: #2A2A2A; color: #5E5E5E; }

QPushButton[chip="true"] { background: %(BG_RAISED)s; color: #DBDBDB;
                           border: 1px solid %(BD_SOFT)s;
                           border-radius: 15px; padding: 7px 16px;
                           font-size: 13px; }
QPushButton[chip="true"]:hover { background: #2D2D2D; border-color: #4F4F4F;
                                 color: #FFFFFF; }
QPushButton[chip="true"]:pressed { background: #1F1F1F; }
QPushButton[chip="true"]:disabled { background: #1A1A1A; color: #4E4E4E;
                                    border-color: #262626; }

QPushButton#mini { background: %(BG_RAISED)s; color: %(TEXT_2)s;
                   border-radius: 7px; padding: 5px 10px; font-size: 12px; }
QPushButton#mini:hover { background: #2F2F2F; color: #EAEAEA; }
QPushButton#mini:pressed { background: #1E1E1E; }
QPushButton#ghost { background: transparent; color: %(TEXT_3)s; border: none;
                    border-radius: 7px; padding: 5px 9px; font-size: 12px; }
QPushButton#ghost:hover { background: #242424; color: #E4E4E4; }
QPushButton#ghost:pressed { background: #1B1B1B; color: #C8C8C8; }
QPushButton#wbtn { background: transparent; color: %(TEXT_3)s; border: none;
                   border-radius: 8px; padding: 0; font-size: 14px; }
QPushButton#wbtn:hover { background: #2A2A2A; color: #EFEFEF; }
QPushButton#wbtn:pressed { background: #1E1E1E; }
QPushButton#wbtn[close="true"]:hover { background: #E5484D; color: #FFFFFF; }
QPushButton#wbtn[close="true"]:pressed { background: #C93A3F; }
QPushButton#pill { background: #202020; color: %(TEXT_2)s;
                   border: 1px solid #2E2E2E; border-radius: 11px;
                   padding: 4px 10px; font-size: 11px; }
QPushButton#pill:hover { background: #2A2A2A; color: #EAEAEA; }

/* 分段控件：轨道与选中块由 SegmentedControl 自绘，按钮只负责文字 */
QWidget#segtrack { background: transparent; }
QPushButton[seg="true"] { background: transparent; color: %(TEXT_2)s;
                          border: none; border-radius: 8px; padding: 4px 12px;
                          font-size: 13px; }
QPushButton[seg="true"]:hover:!checked { color: #E8E8E8; }
QPushButton[seg="true"]:checked { color: #FFFFFF; font-weight: 600;
                                  background: transparent; }

/* ---- 表单 ---- */
QSlider::groove:horizontal { height: 5px; background: #2E2E2E;
                             border-radius: 2px; }
QSlider::sub-page:horizontal { background: #CFCFCF; border-radius: 2px; }
QSlider::handle:horizontal { width: 15px; height: 15px; margin: -5px 0;
                             border-radius: 7px; background: #F4F4F4;
                             border: 1px solid #1B1B1B; }
QSlider::handle:horizontal:hover { background: #FFFFFF; }
QSlider::handle:horizontal:pressed { background: #D0D0D0; }

QCheckBox { color: #CCCCCC; font-size: 13px; spacing: 7px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 5px;
                       background: #242424; border: 1px solid #3C3C3C; }
QCheckBox::indicator:checked { background: #EFEFEF; border: 1px solid #EFEFEF; }

QComboBox { background: #242424; color: #E6E6E6; border: none;
            border-radius: %(R_CTRL)spx; padding: 7px 12px; font-size: 13px; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: #1B1B1B; color: #E6E6E6;
    border: 1px solid #333333; border-radius: %(R_CTRL)spx;
    selection-background-color: #3D3D3D; outline: none; padding: 4px; }

QLineEdit, QPlainTextEdit { background: #1D1D1D; color: #F0F0F0;
                            border: 1px solid #303030;
                            border-radius: %(R_CTRL)spx;
                            padding: 7px 10px;
                            selection-background-color: #4A4A4A;
                            selection-color: #FFFFFF; }
QLineEdit:hover, QPlainTextEdit:hover { border-color: #3C3C3C; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #6E6E6E;
                                        background: #1F1F1F; }

QListWidget { background: #191919; border: 1px solid #2A2A2A;
              border-radius: 10px; outline: none; padding: 5px; }
QListWidget::item { color: #CACACA; padding: 7px 9px; border-radius: 7px;
                    margin: 1px 0; }
QListWidget::item:selected { background: #383838; color: #FFFFFF; }
QListWidget::item:hover:!selected { background: #232323; }

/* ---- 滚动 ---- */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px 2px; }
QScrollBar::handle:vertical { background: #333333; border-radius: 4px;
                              min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #474747; }
QScrollBar:horizontal { background: transparent; height: 9px; }
QScrollBar::handle:horizontal { background: #333333; border-radius: 4px;
                                min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip { background: #242424; color: #E8E8E8; border: 1px solid #3A3A3A;
           border-radius: 8px; padding: 6px 9px; font-size: 12px; }
""" % dict(globals(), R_CARD=str(R_CARD), R_CTRL=str(R_CTRL), R_SM=str(R_SM))

_PX_RE = re.compile(r"(\d+(?:\.\d+)?)px")


def scaled_qss(qss: str, scale: float) -> str:
    """按 DPI 等比放大 QSS 里的 px 数值。

    主程序用 QT_ENABLE_HIGHDPI_SCALING=0 让坐标与物理像素 1:1，
    代价是 Qt 不再自动缩放控件：150% 屏上 12px 字只有 8pt 大小。
    这里把 QSS 的 px 统一乘缩放系数，面板在 100% / 150% 下观感一致。
    """
    if abs(scale - 1.0) < 0.05:
        return qss

    def repl(match):
        return "%dpx" % max(1, round(float(match.group(1)) * scale))

    return _PX_RE.sub(repl, qss)


def px(value: float, scale: float) -> int:
    """逻辑尺寸 -> 当前 DPI 下的物理尺寸。"""
    return max(0, int(round(float(value) * scale)))
