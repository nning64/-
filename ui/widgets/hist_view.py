# -*- coding: utf-8 -*-
"""SCADA 历史曲线查询: 勾选四遥点, 按时间范围画出 yc_history/yx_history 曲线。

左侧列表多点可勾; 右侧 matplotlib 画选中点的历史序列。
范围模式:
  最近N秒    = 以"历史数据最新时刻"为终点, 往前看 N 秒(仿真在跑时窗口自动跟着走)
  自定义区间 = 固定画 t0 ~ t1 之间的数据
改动范围数值会自动刷新(0.3s 防抖), 也可手动点"查询"。
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_API", "pyside6")

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QComboBox,
                               QSpinBox, QPushButton, QSplitter)

from .. import db, theme

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                          "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

_COLORS = [theme.CURVE_WIND, theme.CURVE_WT, theme.CURVE_DG, theme.CURVE_LOAD,
           theme.CURVE_EDIT, theme.BLUE, theme.AMBER, theme.RED,
           theme.GREEN, theme.PURPLE, theme.CYAN]


class HistView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._points = db.history_points()
        self._build()
        self._reload_points()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("范围"))
        self._cb_mode = QComboBox()
        self._cb_mode.addItems(["最近N秒", "自定义区间"])
        self._sp_n = QSpinBox()
        self._sp_n.setRange(10, 100000)
        self._sp_n.setValue(120)
        self._sp_n.setSuffix(" s")
        self._sp_t0 = QSpinBox()
        self._sp_t0.setRange(0, 1000000)
        self._sp_t1 = QSpinBox()
        self._sp_t1.setRange(0, 1000000)
        self._sp_t1.setValue(300)
        self._btn_q = QPushButton("查询")
        self._btn_c = QPushButton("清空")
        top.addWidget(self._cb_mode)
        top.addWidget(self._sp_n)
        top.addWidget(self._sp_t0)
        top.addWidget(QLabel("~"))
        top.addWidget(self._sp_t1)
        top.addWidget(self._btn_q)
        top.addWidget(self._btn_c)
        top.addStretch(1)
        root.addLayout(top)

        hint = QLabel(
            "范围说明: 最近N秒 = 以数据最新时刻为终点往前看; "
            "自定义区间 = 固定画 t0~t1。改动范围自动刷新。")
        hint.setObjectName("sub")
        hint.setWordWrap(True)
        root.addWidget(hint)

        split = QSplitter(Qt.Horizontal)
        self._lst = QListWidget()
        self._lst.setSelectionMode(QListWidget.MultiSelection)
        self._lst.setFixedWidth(240)
        self._fig = Figure(figsize=(7, 5), dpi=100)
        self._fig.patch.set_facecolor(theme.BG)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_facecolor(theme.BG)
        self._ax.tick_params(colors=theme.SUB, labelsize=9)
        for sp in self._ax.spines.values():
            sp.set_color(theme.BORDER)
        self._fig.subplots_adjust(left=0.11, right=0.96, top=0.92,
                                  bottom=0.13)
        self._canvas = FigureCanvas(self._fig)
        split.addWidget(self._lst)
        split.addWidget(self._canvas)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # 防抖定时器: 改范围数值后 0.3s 内不再触发就自动查一次
        self._deb = QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(300)
        self._deb.timeout.connect(self._query)

        self._btn_q.clicked.connect(self._query)
        self._btn_c.clicked.connect(self._clear)
        self._cb_mode.currentIndexChanged.connect(self._on_mode)
        self._sp_n.valueChanged.connect(self._on_recent_changed)
        self._sp_t0.valueChanged.connect(self._on_custom_changed)
        self._sp_t1.valueChanged.connect(self._on_custom_changed)

    # ---------- 信号 ----------

    def _on_mode(self):
        """切换模式: 启用对应的输入框并立即查一次。"""
        is_recent = self._cb_mode.currentIndex() == 0
        self._sp_n.setEnabled(is_recent)
        self._sp_t0.setEnabled(not is_recent)
        self._sp_t1.setEnabled(not is_recent)
        self._query()

    def _on_recent_changed(self, _=None):
        # setValue 由程序回写时也会触发, 只在"最近N秒"模式且输入可用时刷新
        if self._cb_mode.currentIndex() == 0 and self._sp_n.isEnabled():
            self._deb.start()

    def _on_custom_changed(self, _=None):
        if self._cb_mode.currentIndex() == 1:
            self._deb.start()

    # ---------- 数据 ----------

    def _reload_points(self):
        self._lst.clear()
        for p in self._points:
            lab = "%s  %s  %s" % (p["rtu"], p["type"], p["name"])
            self._lst.addItem(QListWidgetItem(lab))

    def _hint_text(self, msg):
        self._ax.cla()
        self._ax.set_facecolor(theme.BG)
        self._ax.text(0.5, 0.5, msg, fontsize=12, ha="center", va="center",
                      color=theme.SUB, transform=self._ax.transAxes)
        self._canvas.draw_idle()

    def _query(self):
        sel = self._lst.selectedItems()
        if not sel:
            self._hint_text("请先在左侧勾选要看的点")
            return

        # 时间范围
        max_t = db.history_max_t()
        if self._cb_mode.currentIndex() == 0:
            n = int(self._sp_n.value())
            if max_t <= 0:
                self._hint_text("暂无历史数据, 先跑 main.py 让仿真落数据")
                return
            t1 = max_t
            t0 = max(0, t1 - n)
        else:
            t0 = int(self._sp_t0.value())
            t1 = int(self._sp_t1.value())
            if t1 < t0:
                t0, t1 = t1, t0

        # 对应选中项的点坐标
        pairs = []
        for item in sel:
            idx = self._lst.row(item)
            pairs.append(self._points[idx])

        self._ax.cla()
        self._ax.set_facecolor(theme.BG)
        self._ax.grid(True, color="#2a3750", linestyle="-", linewidth=0.6,
                      alpha=0.8)
        self._ax.tick_params(colors=theme.SUB, labelsize=9)
        for sp in self._ax.spines.values():
            sp.set_color(theme.BORDER)
        self._ax.set_xlabel("仿真时间 (s)", color=theme.SUB, fontsize=9,
                            labelpad=8)
        self._ax.set_ylabel("值", color=theme.SUB, fontsize=9, labelpad=9)
        self._ax.set_xlim(t0, t1)
        for i, p in enumerate(pairs):
            series = db.history_series(p["rtu"], p["type"], p["pt"], t0, t1)
            if not series:
                continue
            xs = [s[0] for s in series]
            ys = [s[1] for s in series]
            c = _COLORS[i % len(_COLORS)]
            self._ax.plot(xs, ys, color=c, lw=1.5,
                          label="%s %s" % (p["name"], p["type"]))
        if self._ax.get_lines():
            self._ax.legend(loc="upper left", fontsize=8, framealpha=0.15,
                            labelcolor=theme.TEXT)
        else:
            self._ax.text(0.5, 0.5,
                          "该区间没有记录 (历史数据范围 0~%ds)" % max_t,
                          fontsize=11, ha="center", va="center",
                          color=theme.SUB, transform=self._ax.transAxes)
        self._canvas.draw_idle()

    def _clear(self):
        self._lst.clearSelection()
        self._hint_text("已清空")
