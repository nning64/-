# -*- coding: utf-8 -*-
"""实时曲线: matplotlib FigureCanvas 嵌入 Qt。

双子图:
  ax_env  环境输入 — 风速(蓝, 左轴 m/s)、负荷(橙, 右轴 kW)
  ax_pow  功率平衡 — 负荷(橙虚线)、风机出力(绿)、柴发出力(红), 全 kW
数据来自 sim_history 最近 window 秒。T21 的"拖拽编辑"会在这之上叠加控制点。
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_API", "pyside6")

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PySide6.QtWidgets import QWidget, QVBoxLayout

from .. import theme

# 中文字体: 微软雅黑(无则回退黑体/默认)
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                          "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


class CurveView(QWidget):
    def __init__(self, window: int = 180, parent=None):
        super().__init__(parent)
        self.window_sec = window     # 显示最近多少仿真秒(公开, 供外层查询)
        self._empty_note = None

        self._fig = Figure(figsize=(8, 6), dpi=100)
        self._fig.patch.set_facecolor(theme.BG)
        # 左右边距加大: 左给"风速 (m/s)"让位避免贴边被裁; 右给"负荷 (kW)"让位
        self._fig.subplots_adjust(left=0.14, right=0.85, top=0.90,
                                  bottom=0.12, hspace=0.38)

        self._ax_env = self._fig.add_subplot(211)
        self._ax_pow = self._fig.add_subplot(212)
        for ax in (self._ax_env, self._ax_pow):
            ax.set_facecolor(theme.BG)
            ax.grid(True, color="#2a3750", linestyle="-", linewidth=0.6,
                    alpha=0.8)
            ax.tick_params(colors=theme.SUB, labelsize=9)
            for sp in ax.spines.values():
                sp.set_color(theme.BORDER)
        # 环境子图: 负荷用右侧 twinx
        self._ax_load = self._ax_env.twinx()
        self._ax_load.set_facecolor(theme.BG)
        self._ax_load.tick_params(colors=theme.CURVE_LOAD, labelsize=9)
        self._ax_load.spines["right"].set_color(theme.BORDER)
        # 明确把负荷标签钉在右轴(否则后续 cla() 会把 label 位置重置回"左",
        # 导致"负荷 (kW)"跑到左边跟"风速 (m/s)"叠在一起)
        self._ax_load.yaxis.set_label_position("right")

        self._canvas = FigureCanvas(self._fig)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._canvas)

    # ---------------- 刷新 ----------------
    def refresh(self, rows: list[dict]):
        """rows: db.recent_sim() 返回的升序行。全量重绘(数据量小, 够快)。"""
        self._ax_env.cla()
        self._ax_pow.cla()
        # 重设配色/边框/网格( cla 会清掉)
        for ax in (self._ax_env, self._ax_pow):
            ax.set_facecolor(theme.BG)
            ax.grid(True, color="#2a3750", linestyle="-", linewidth=0.6,
                    alpha=0.8)
            ax.tick_params(colors=theme.SUB, labelsize=9)
            for sp in ax.spines.values():
                sp.set_color(theme.BORDER)

        if not rows:
            self._ax_env.text(0.5, 0.5, "等待仿真数据…\n(先跑 main.py)",
                              transform=self._ax_env.transAxes,
                              ha="center", va="center",
                              color=theme.SUB, fontsize=13)
            self._canvas.draw_idle()
            return

        t = [r["sim_time"] for r in rows]
        wind = [r["wind_speed"] for r in rows]
        load = [r["load_kw"] for r in rows]
        wt = [r["wt_act_kw"] for r in rows]
        dg = [r["dg_act_kw"] for r in rows]

        # 环境子图: 风速(左) + 负荷(右 twinx)
        w_line, = self._ax_env.plot(t, wind, color=theme.CURVE_WIND, lw=1.6,
                                    label="风速")
        self._ax_env.set_ylabel("风速 (m/s)", color=theme.CURVE_WIND,
                                fontsize=10, labelpad=14)
        self._ax_env.set_ylim(0, max(30, max(wind or [0]) * 1.2))
        self._ax_load.cla()
        # cla() 会把(右轴)负荷标签位置重置回"左", 必须再钉回右,
        # 否则"负荷 (kW)"跑左边跟"风速 (m/s)"重叠 —— 2026-09-08 修复
        self._ax_load.yaxis.set_label_position("right")
        self._ax_load.plot(t, load, color=theme.CURVE_LOAD, lw=1.6,
                           label="负荷")
        self._ax_load.set_ylabel("负荷 (kW)", color=theme.CURVE_LOAD,
                                 fontsize=10, labelpad=14)
        self._ax_load.set_ylim(0, max(380, max(load or [0]) * 1.15))
        self._ax_load.tick_params(colors=theme.CURVE_LOAD, labelsize=9)
        self._ax_load.spines["right"].set_color(theme.BORDER)
        self._ax_env.legend(handles=[w_line], loc="upper left", fontsize=9,
                            framealpha=0.15, labelcolor=theme.CURVE_WIND)

        # 功率子图
        self._ax_pow.plot(t, load, color=theme.CURVE_LOAD, lw=1.2,
                          ls="--", label="负荷")
        self._ax_pow.plot(t, wt, color=theme.CURVE_WT, lw=1.8, label="风机")
        self._ax_pow.plot(t, dg, color=theme.CURVE_DG, lw=1.8, label="柴发")
        self._ax_pow.set_ylabel("功率 (kW)", color=theme.SUB, fontsize=10,
                                labelpad=9)
        self._ax_pow.set_xlabel("仿真时间 (s)", color=theme.SUB, fontsize=10,
                                labelpad=8)
        self._ax_pow.set_ylim(0, max(380, max(load or [0]) * 1.15))
        self._ax_pow.legend(loc="upper left", fontsize=9, framealpha=0.15,
                            labelcolor=theme.TEXT)

        self._ax_env.set_xlim(max(0, (t[-1] if t else 0) - self.window_sec),
                              t[-1] if t else self.window_sec)
        self._ax_pow.set_xlim(self._ax_env.get_xlim())
        self._canvas.draw_idle()
