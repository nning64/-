# -*- coding: utf-8 -*-
"""实时曲线: matplotlib FigureCanvas 嵌入 Qt。

双子图:
  ax_env  环境输入 — 风速(蓝, 左轴 m/s)、负荷(橙, 右轴 kW)
  ax_pow  功率平衡 — 负荷(橙虚线)、风机出力(绿)、柴发出力(红), 全 kW
数据来自 sim_history 最近 window 秒。

拖拽编辑(T21 验收):
- ax_env 子图上按住鼠标左键 -> 改未来时刻 env_curve.wind_speed(读左轴 y)
- ax_env 子图上按住鼠标右键 -> 改未来时刻 env_curve.load_kw(读右轴 y, ax_load)
- x 轴坐标 -> 取最近整秒 sim_time t
- y 值钳到合理范围(wind 0~30, load 0~400)
- 拖拽时画一个 marker(竖虚线 + 小圆点), 松开后调 db.write_env_point
  写回 env_curve; 主循环下一秒就会读到新值, 不需重启。
- 每次 refresh() 重绘曲线时清空历史已编辑 marker, 保证视觉一致。
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_API", "pyside6")

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout

from .. import db, theme

# 中文字体: 微软雅黑(无则回退黑体/默认)
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                          "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

# 拖拽边界 —— 防止误拖到非物理值
WIND_MIN, WIND_MAX = 0.0, 30.0
LOAD_MIN, LOAD_MAX = 0.0, 400.0


class CurveView(QWidget):
    # 松开鼠标后发出的信号, MainWindow 在 _mount_curve 里 connect 到 statusBar。
    # payload: ("ok", t, field, value) 或 ("warn", msg)
    envEdited = Signal(object)

    def __init__(self, window: int = 180, parent=None):
        super().__init__(parent)
        self.window_sec = window     # 显示最近多少仿真秒(公开, 供外层查询)
        self._empty_note = None
        # 拖拽状态: None=无拖动; dict{t, value, field, marker_lines, marker_pts}
        self._drag = None
        # 已确认的拖拽结果(刷新曲线后还会保留): 每项 (axvline, scatter, field, t)
        self._edited = []

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

        # ---------- 拖拽事件绑定 ----------
        # mpl_connect 返回 cid, 这里不存(FigureCanvas 销毁时会自动解绑)
        self._canvas.mpl_connect("button_press_event",   self._on_press)
        self._canvas.mpl_connect("motion_notify_event",  self._on_move)
        self._canvas.mpl_connect("button_release_event", self._on_release)

    # ---------------- 拖拽事件 ----------------
    def _on_press(self, event):
        """按下鼠标: 进入拖拽态, 仅在 ax_env/ax_load 上响应(功率图是历史, 不能改)。"""
        if event.inaxes is None or event.button not in (1, 3):
            return
        if event.inaxes not in (self._ax_env, self._ax_load):
            return
        # 1=左键改 wind_speed(用左轴 y); 3=右键改 load_kw(用右轴 y)
        if event.button == 1 and event.inaxes is self._ax_env:
            field = "wind_speed"
            color = theme.CURVE_WIND
            ymin, ymax = WIND_MIN, WIND_MAX
        elif event.button == 3 and event.inaxes in (self._ax_env, self._ax_load):
            field = "load_kw"
            color = theme.CURVE_LOAD
            ymin, ymax = LOAD_MIN, LOAD_MAX
        else:
            return      # 其他组合(左键在 ax_load / 右键在 ax_env)忽略

        # 防止拖到已过去的时间点(改历史无意义); 取 max(0, xdata) 再 round
        t = int(round(event.xdata))
        if t < 0:
            t = 0
        value = float(event.ydata)
        value = max(ymin, min(ymax, value))

        self._drag = {
            "t": t, "value": value, "field": field, "color": color,
            "ymin": ymin, "ymax": ymax,
            "vline": None, "scatter": None,
        }
        self._redraw_drag_marker()

    def _on_move(self, event):
        """拖拽过程中: 只刷新 marker, 不写库。"""
        if self._drag is None or event.inaxes is None:
            return
        # 允许跨轴移动(左键时偶尔可能滑到 ax_load 上) —— 取 ydata 时按 field 决定
        if self._drag["field"] == "wind_speed":
            ymin, ymax = self._drag["ymin"], self._drag["ymax"]
        else:
            ymin, ymax = self._drag["ymin"], self._drag["ymax"]
        if event.ydata is None:
            return
        v = max(ymin, min(ymax, float(event.ydata)))
        # t 也可随拖动变(横向挪动时跟着挪), 但简单起见按下时锁死 t
        self._drag["value"] = v
        self._redraw_drag_marker()

    def _on_release(self, event):
        """松开鼠标: 调 db.write_env_point 写 env_curve, 并把 marker 升级成永久标记。

        关键改进(2026-09-10): 写库后做三件事——
        1. 看 UPDATE 影响行数: 0 = t 不在 env_curve 范围, warn 给用户
        2. 看当前 sim_time: t <= sim_time = 改的是过去, warn 给用户(虽然写了但不生效)
        3. 看 sim_state: 不是 1(运行) = 仿真冻结, 改了也不会被读到, warn 给用户
        三种情况都通过 envEdited 信号打 status bar, 让用户立刻看到"成功/无效/无效"
        而不是只看到一个 marker 闪一下就以为啥也没发生。
        """
        if self._drag is None:
            return
        t, value, field, color = (self._drag["t"], self._drag["value"],
                                  self._drag["field"], self._drag["color"])
        # 1. 写库, 拿到 affected_rows 判断 t 是否在 env_curve 范围内
        affected = 0
        err = None
        try:
            with db._conn(ro=False) as c:
                cur = c.execute(
                    "UPDATE env_curve SET %s = ? WHERE t = ?"
                    % field, (float(value), int(t))
                )
                affected = cur.rowcount
        except Exception as e:
            err = str(e)

        if err:
            self.envEdited.emit(("warn",
                f"写库失败 t={t} {field}={value}: {err}"))
        elif affected == 0:
            # t 不在 env_curve 范围(场景 0~60, 拖到 80)—— UPDATE 影响 0 行
            rng = self._env_range()
            if rng:
                lo, hi = rng
                self.envEdited.emit(("warn",
                    f"t={t} 超出场景范围 {lo}~{hi}, 已忽略"))
            else:
                self.envEdited.emit(("warn",
                    f"t={t} 在 env_curve 里查无此点, 已忽略"))
        else:
            # 写成功, 再看是否"生效"
            sim_t, sim_state = self._sim_state()
            warn = []
            if sim_t is not None and t <= sim_t:
                warn.append(f"t={t} ≤ 当前 sim_time={sim_t}(改的是历史)")
            if sim_state is not None and sim_state != 1:
                state_name = {0: "停止", 2: "暂停"}.get(sim_state, f"状态{sim_state}")
                warn.append(f"仿真{state_name}(sim_state={sim_state}),改了也不会被读到")
            if warn:
                self.envEdited.emit(("warn",
                    f"已写 env_curve t={t} {field}={value:.2f}, 但"
                    + "; ".join(warn)))
            else:
                self.envEdited.emit(("ok",
                    f"已改 env_curve t={t} {field}={value:.2f} "
                    f"(下一秒生效, 当前 sim_time={sim_t})"))

        # 把当前临时 marker(若有)升级为永久 marker, 画在 ax_env 上
        if self._drag["vline"] is not None:
            self._edited.append((self._drag["vline"], self._drag["scatter"],
                                 field, t))
        # 触发一次重绘让 marker 留在画上
        self._drag = None
        self._canvas.draw_idle()

    def _env_range(self):
        """env_curve 实际行范围(min_t, max_t), 用于越界提示; 失败返回 None。"""
        try:
            with db._conn(ro=True) as c:
                row = c.execute(
                    "SELECT min(t), max(t) FROM env_curve"
                ).fetchone()
            if row and row[0] is not None:
                return int(row[0]), int(row[1])
        except Exception:
            pass
        return None

    def _sim_state(self):
        """读 sim_params(sim_time, sim_state)。None = 读不到。"""
        try:
            with db._conn(ro=True) as c:
                rt = c.execute(
                    "SELECT value FROM sim_params WHERE key='sim_time'"
                ).fetchone()
                rs = c.execute(
                    "SELECT value FROM sim_params WHERE key='sim_state'"
                ).fetchone()
            sim_t = int(rt[0]) if rt and rt[0] is not None else None
            sim_s = int(rs[0]) if rs and rs[0] is not None else None
            return sim_t, sim_s
        except Exception:
            return None, None

    def _redraw_drag_marker(self):
        """根据 self._drag 状态画/更新一个 marker: 竖虚线 + 小圆点。"""
        if self._drag is None:
            return
        d = self._drag
        # 删旧的
        if d["vline"] is not None:
            try:
                d["vline"].remove()
            except Exception:
                pass
        if d["scatter"] is not None:
            try:
                d["scatter"].remove()
            except Exception:
                pass
        # 画新的 —— vline 画在 ax_env(双轴共享 x); scatter 画在对应子图
        vline = self._ax_env.axvline(d["t"], color=d["color"],
                                     linestyle=":", linewidth=1.0, alpha=0.85)
        if d["field"] == "wind_speed":
            # 左轴 scatter
            scatter = self._ax_env.scatter([d["t"]], [d["value"]],
                                           color=d["color"], s=40, zorder=5)
        else:
            # 右轴 scatter
            scatter = self._ax_load.scatter([d["t"]], [d["value"]],
                                           color=d["color"], s=40, zorder=5)
        d["vline"] = vline
        d["scatter"] = scatter
        self._canvas.draw_idle()

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
        # 重绘后清掉旧的 edited 列表(marker 已被 cla() 清掉), 让用户重新拖
        # —— 但拖完的"我改过哪些点"信息丢失是个体验问题; 这里取最简方案:
        # 只在本次拖拽过程中保留 marker, refresh 后丢弃, 因为 sim_history
        # 已经被主循环读到新值并画进曲线了, 等同于"已合并"。
        self._edited.clear()
        # 如果正在拖拽中, 把临时 marker 重新画上
        if self._drag is not None:
            self._redraw_drag_marker()
        else:
            self._canvas.draw_idle()