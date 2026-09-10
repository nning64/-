# -*- coding: utf-8 -*-
"""深色"调度监控屏"主题色板与全局 QSS。

集中放颜色/QSS, 各控件引用 theme 里的常量, 想换配色只改这一个文件。
QSS 是普通字符串(不用 f-string), 避免 {} 被 f-string 吃掉。
"""
from __future__ import annotations

# ---- 色板 ----
BG      = "#0f1420"   # 窗口底色
PANEL   = "#1a2233"   # 卡片/面板底色
PANEL2  = "#222c42"   # 面板内嵌
BORDER  = "#2e3a54"   # 边框
TEXT    = "#dfe6f3"   # 主文字
SUB     = "#8ea0bf"   # 次要文字
GREEN   = "#2ecc71"   # 运行/连接/正常
RED     = "#e74c3c"   # 停机/断开/故障/越限
AMBER   = "#f39c12"   # 告警/未定
BLUE    = "#3498db"   # 曲线/强调
CYAN    = "#1abc9c"
PURPLE  = "#9b59b6"
GRAY    = "#5b6b85"   # 灯熄灭灰

# 曲线配色
CURVE_WIND  = "#3498db"   # 风速
CURVE_LOAD  = "#f39c12"   # 负荷
CURVE_WT    = "#2ecc71"   # 风机出力
CURVE_DG    = "#e74c3c"   # 柴发出力
CURVE_EDIT  = "#9b59b6"   # 可拖拽控制点

FONT_FAMILY = "Microsoft YaHei"   # 微软雅黑, Windows 必有

_QSS = """
QMainWindow, QWidget#root { background-color: %(bg)s; }
QLabel { color: %(text)s; font-family: "%(font)s"; font-size: 13px; }
QLabel#sub  { color: %(sub)s; font-size: 12px; }
QLabel#big  { font-size: 26px; font-weight: bold; }
QLabel#title{ font-size: 17px; font-weight: bold; }
QGroupBox {
    background-color: %(panel)s; border: 1px solid %(border)s;
    border-radius: 8px; margin-top: 12px; padding-top: 4px;
    font-family: "%(font)s"; font-size: 14px; font-weight: bold; color: %(text)s;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTableWidget {
    background-color: %(panel2)s; color: %(text)s; border: 1px solid %(border)s;
    gridline-color: %(border)s; font-family: "%(font)s"; font-size: 13px;
}
QHeaderView::section {
    background-color: %(panel)s; color: %(sub)s; border: none;
    border-bottom: 1px solid %(border)s; padding: 4px;
    font-family: "%(font)s"; font-size: 12px; font-weight: bold;
}
QPushButton {
    background-color: %(panel2)s; color: %(text)s; border: 1px solid %(border)s;
    border-radius: 6px; padding: 6px 14px; font-family: "%(font)s"; font-size: 13px;
}
QPushButton:hover { border-color: %(blue)s; }
QPushButton:pressed { background-color: %(blue)s; }
QPushButton#danger:hover { border-color: %(red)s; }
QStatusBar { background-color: %(panel)s; color: %(sub)s; font-size: 12px; }
QPlainTextEdit {
    background-color: %(panel2)s; color: %(text)s; border: 1px solid %(border)s;
    border-radius: 4px; font-family: Consolas; font-size: 12px;
}
QScrollBar:vertical { background: %(panel)s; width: 10px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 5px; }
QSplitter::handle { background-color: transparent; }
QDockWidget { color: %(text)s; font-family: "%(font)s"; font-size: 13px; }
QDockWidget::title {
    background-color: %(panel)s; padding: 4px 8px; text-align: left; font-weight: bold;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: %(panel2)s; color: %(text)s; border: 1px solid %(border)s;
    border-radius: 4px; padding: 3px 6px; font-family: "%(font)s"; font-size: 13px;
    selection-background-color: %(blue)s; selection-color: %(bg)s;
}
QComboBox QAbstractItemView {
    background-color: %(panel2)s; color: %(text)s;
    selection-background-color: %(blue)s; selection-color: %(bg)s;
}
QCheckBox { color: %(text)s; font-family: "%(font)s"; font-size: 13px; }
QMenuBar { background-color: %(panel)s; color: %(text)s; font-family: "%(font)s"; font-size: 13px; }
QMenu { background-color: %(panel)s; color: %(text)s; font-family: "%(font)s"; font-size: 13px; }
QMenu::item:selected { background-color: %(blue)s; color: %(bg)s; }
""" % {"bg": BG, "panel": PANEL, "panel2": PANEL2, "border": BORDER,
       "text": TEXT, "sub": SUB, "blue": BLUE, "red": RED, "font": FONT_FAMILY}

QSS = _QSS   # 全局样式表(公开别名)


def lamp_style(color: str, size: int = 14) -> str:
    """返回一个"状态灯"圆点的 QSS: 直径 size, 颜色 color(熄灭可用 GRAY)。"""
    d = size // 2
    return ("QLabel { background-color: %s; border-radius: %dpx;"
            " min-width: %dpx; max-width: %dpx; min-height: %dpx;"
            " max-height: %dpx; }") % (color, d, size, size, size, size)


def chip_style(fg: str, bgc: str) -> str:
    """返回一个"状态胶囊"的 QSS。"""
    return ("QLabel { color: %s; background-color: %s; border-radius: 9px;"
            " padding: 1px 10px; font-size: 12px; font-weight: bold; }"
            ) % (fg, bgc)
