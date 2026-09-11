# -*- coding: utf-8 -*-
"""实时曲线: matplotlib FigureCanvas 嵌入 Qt。

双子图:
  ax_env  环境输入 — 风速(蓝, 左轴 m/s)、负荷(橙, 右轴 kW); 画 env_curve 剧本,
          按周期循环铺满窗口, 全实线, 可拖拽改
  ax_pow  功率平衡 — 负荷(橙)、风机出力(绿)、柴发出力(红), 全 kW(实线, 回放)

拖拽编辑(T21 验收):
- 环境子图把 env_curve(剧本) 按 "周期 = max_t + 1" 循环铺满整个可见窗口, 全部
  用实线画 —— 所以曲线任何时候都从左端连续到右端, 不会只画到 60s 就断。
  (main.py 默认 --loop, 用 t % period 取剧本, 这个铺法和仿真实际取值一致)
- "现在"红线固定在 x 轴正中央, 窗口随仿真推进整体右移 -> 曲线一直往左移
  (示波器式), 过去在左(灰色阴影=锁定)/未来在右(可改)。现在线永远居中,
  不会卡在右端停住(2026-09-11 修复)。
- 左键拖 -> 改 env_curve.wind_speed(风速, 左轴); 右键拖 -> 改 env_curve.load_kw
  (负荷, 右轴)。字段由鼠标按键决定, 不依赖 event.inaxes —— 因为 ax_load(twinx)
  创建在 ax_env 之后、叠在上面, 整片环境的点击 inaxes 都是 ax_load, 原"左键必须
  落在 ax_env"的分支进不去、风速改不了(2026-09-11 修复)。
- x 坐标取最近整秒 t; 仅当 t > 按下时刻 sim_time(未来)才写库, 历史点直接拒绝。
  写库时按 t % period 折回剧本对应格(与 main 循环取法一致), 所以拖 t>60 的未来点
  也能落到 env_curve 上、下一帧生效。
- y 值钳到合理范围(wind 0~30, load 0~400); 各字段用各自轴把像素 y 反算成数据 y。
- 拖拽时画 marker(竖虚线+小圆点), 松手写回 env_curve; main 每帧按 t 读 env_curve[t],
  未来改动下一帧自动生效。
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
        # 居中滚动窗口的半宽(秒): 可见总宽 = 2*_view_half, "现在"固定在正中央。
        # 与 history 拉取量(window_sec)解耦, 单独取 60 保证拖拽时间分辨率够细。
        self._view_half = 60
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
        """按下鼠标: 进入拖拽态。

        修复(2026-09-11): 字段由鼠标按键决定, 不依赖 event.inaxes —— 因为
        ax_load(twinx) 创建在 ax_env 之后、叠在上面, 整片环境子图的点击
        event.inaxes 都是 ax_load, 原"左键必须落在 ax_env"的分支永远进不去、
        风速改不了。改用按键选字段, 再用各自轴的变换把像素 y 反算成数据 y
        (风速用左轴、负荷用右轴), 彻底绕开 twinx 遮挡导致的 y 轴错乱。
        """
        if event.button not in (1, 3):
            return
        if event.inaxes not in (self._ax_env, self._ax_load):
            return
        if event.xdata is None:
            return
        # 用按键选字段; y 值按各自轴从像素反算(避开 twinx 遮挡导致 event.ydata
        # 落在错误轴的问题)
        if event.button == 1:                      # 左键 -> 风速(左轴)
            field, color = "wind_speed", theme.CURVE_WIND
            ymin, ymax = WIND_MIN, WIND_MAX
            yval = self._y_on_axis(self._ax_env, event)
        else:                                       # 右键 -> 负荷(右轴)
            field, color = "load_kw", theme.CURVE_LOAD
            ymin, ymax = LOAD_MIN, LOAD_MAX
            yval = self._y_on_axis(self._ax_load, event)
        if yval is None:
            return
        # 时间轴两轴共享, xdata 一致; 取最近整秒(下限 0)
        t = int(round(event.xdata))
        if t < 0:
            t = 0
        value = max(ymin, min(ymax, float(yval)))

        # 按下时刻的 sim_time 作为"历史/未来"判定基准: 拖拽期间即使仿真推进,
        # 你按下那一刻认定的"未来"仍算未来, 不会中途变卦(2026-09-11)。
        sim_t0, _ = self._sim_state()

        self._drag = {
            "t": t, "value": value, "field": field, "color": color,
            "ymin": ymin, "ymax": ymax, "sim_t0": sim_t0,
            "vline": None, "scatter": None,
        }
        self._redraw_drag_marker()

    def _y_on_axis(self, ax, event):
        """把鼠标像素坐标(y)反算成 ax 的数据坐标 y。twinx 遮挡时也能拿到正确轴的值。"""
        try:
            pt = ax.transData.inverted().transform((event.x, event.y))
            return float(pt[1])
        except Exception:
            return None

    def _on_move(self, event):
        """拖拽过程中: 只刷新 marker, 不写库。y 用各自轴从像素反算。"""
        if self._drag is None or event.inaxes is None:
            return
        ax = self._ax_env if self._drag["field"] == "wind_speed" else self._ax_load
        yval = self._y_on_axis(ax, event)
        if yval is None:
            return
        ymin, ymax = self._drag["ymin"], self._drag["ymax"]
        self._drag["value"] = max(ymin, min(ymax, float(yval)))
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
        # 0. 边界: 只能改未来(t > 按下时的 sim_time); 历史点直接拒绝, 不写库
        sim_t0 = self._drag.get("sim_t0", 0)
        if t < sim_t0:
            self.envEdited.emit(("warn",
                f"t={t} < 当前 sim_time={sim_t0}（历史时刻, 不能改）, 已忽略"))
            self._drag = None
            self._canvas.draw_idle()
            return
        # 1. 写库。剧本是循环的(main.py 默认 --loop, 用 t % period 取剧本),
        #    所以显示在 t > max_t 的未来点, 实际改写的是 env_curve 里 t % period
        #    那一格 —— 这样拖动"铺满后超出 60s 的那段"也能落到库上、下一帧生效。
        #    period = max_t + 1, 与 main.py 的模长一致。 (2026-09-11)
        period = self._period()
        write_t = (t % period) if period > 0 else t
        where = f"t={t}" if write_t == t else f"t={t}(剧本第{write_t}秒)"
        affected = 0
        err = None
        try:
            with db._conn(ro=False) as c:
                cur = c.execute(
                    "UPDATE env_curve SET %s = ? WHERE t = ?"
                    % field, (float(value), int(write_t))
                )
                affected = cur.rowcount
        except Exception as e:
            err = str(e)

        if err:
            self.envEdited.emit(("warn",
                f"写库失败 {where} {field}={value}: {err}"))
        elif affected == 0:
            # 只有 env_curve 为空时才会走到(有剧本时 t%period 一定命中)
            rng = self._env_range()
            if rng:
                lo, hi = rng
                self.envEdited.emit(("warn",
                    f"{where} 超出场景范围 {lo}~{hi}, 已忽略"))
            else:
                self.envEdited.emit(("warn",
                    f"{where} 在 env_curve 里查无此点, 已忽略"))
        else:
            # 写成功, 再看是否"生效"
            sim_t, sim_state = self._sim_state()
            warn = []
            if sim_state is not None and sim_state != 1:
                state_name = {0: "停止", 2: "暂停"}.get(sim_state, f"状态{sim_state}")
                warn.append(f"仿真{state_name}(sim_state={sim_state}),改了也不会被读到")
            if warn:
                self.envEdited.emit(("warn",
                    f"已写 env_curve {where} {field}={value:.2f}, 但"
                    + "; ".join(warn)))
            else:
                self.envEdited.emit(("ok",
                    f"已改 env_curve {where} {field}={value:.2f} "
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

    def _period(self):
        """剧本循环周期 = env_curve max_t + 1(与 main.py 的 loop 模长一致); 空则 0。"""
        rng = self._env_range()
        return (rng[1] + 1) if rng else 0

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
        """rows: db.recent_sim() 返回的升序行(实际回放)。全量重绘。

        设计(2026-09-11, 第三版):
        - 环境子图把 env_curve(剧本) 按 "周期 = max_t + 1" 循环铺满整个可见窗口,
          全部实线 -> 曲线任何时候都从左端连续到右端, 不会只到 60s 就断。
          (main.py 默认 --loop, 用 t % period 取剧本, 铺法与实际取值一致)
        - "现在"红线固定在 x 轴正中央, 窗口随仿真推进整体右移 -> 曲线一直往左移;
          过去在左(灰色阴影=锁定)/未来在右(可改)。
        - 功率子图仍是 sim_history 实线回放。
        """
        self._ax_env.cla()
        self._ax_pow.cla()
        # 重设配色/边框/网格(cla 会清掉)
        for ax in (self._ax_env, self._ax_pow):
            ax.set_facecolor(theme.BG)
            ax.grid(True, color="#2a3750", linestyle="-", linewidth=0.6,
                    alpha=0.8)
            ax.tick_params(colors=theme.SUB, labelsize=9)
            for sp in ax.spines.values():
                sp.set_color(theme.BORDER)

        sim_t, _ = self._sim_state()
        plan = db.env_curve()          # 全量剧本: t / wind_speed / load_kw

        # 居中滚动窗口: 先定范围(剧本要按它铺满), 末尾再 set_xlim
        now = sim_t if sim_t is not None else 0
        half = self._view_half
        xmin, xmax = int(now - half), int(now + half)

        # ---------- 环境子图: 剧本按周期循环铺满整个窗口(全实线) ----------
        if plan:
            base_w = [r["wind_speed"] for r in plan]
            base_l = [r["load_kw"] for r in plan]
            period = plan[-1]["t"] + 1     # 与 main.py 的 loop 模长一致
            xs = list(range(xmin, xmax + 1))
            pw = [base_w[t % period] for t in xs]
            pl = [base_l[t % period] for t in xs]

            # 风速(左轴, 蓝, 实线)
            w_line, = self._ax_env.plot(xs, pw, color=theme.CURVE_WIND, lw=1.8,
                                        label="剧本风速")
            self._ax_env.set_ylabel("风速 (m/s)", color=theme.CURVE_WIND,
                                    fontsize=10, labelpad=14)
            self._ax_env.set_ylim(0, max(30, max(pw) * 1.2))

            # 负荷(右轴 twinx, 橙, 实线)
            self._ax_load.cla()
            self._ax_load.yaxis.set_label_position("right")
            l_line, = self._ax_load.plot(xs, pl, color=theme.CURVE_LOAD, lw=1.8,
                                         label="剧本负荷")
            self._ax_load.set_ylabel("负荷 (kW)", color=theme.CURVE_LOAD,
                                     fontsize=10, labelpad=14)
            self._ax_load.set_ylim(0, max(380, max(pl) * 1.15))
            self._ax_load.tick_params(colors=theme.CURVE_LOAD, labelsize=9)
            self._ax_load.spines["right"].set_color(theme.BORDER)

            self._ax_env.legend(handles=[w_line, l_line], loc="upper left",
                                fontsize=9, framealpha=0.15,
                                labelcolor=theme.TEXT)
        else:
            # 没有剧本: 退回只画回放的逻辑
            self._ax_env.set_ylabel("风速 (m/s)", color=theme.CURVE_WIND,
                                    fontsize=10, labelpad=14)
            self._ax_load.cla()
            self._ax_load.yaxis.set_label_position("right")
            self._ax_load.set_ylabel("负荷 (kW)", color=theme.CURVE_LOAD,
                                     fontsize=10, labelpad=14)
            self._ax_load.tick_params(colors=theme.CURVE_LOAD, labelsize=9)
            self._ax_load.spines["right"].set_color(theme.BORDER)

        # ---------- 功率子图: sim_history 实际回放(全实线) ----------
        if rows:
            t = [r["sim_time"] for r in rows]
            wind = [r["wind_speed"] for r in rows]
            load = [r["load_kw"] for r in rows]
            wt = [r["wt_act_kw"] for r in rows]
            dg = [r["dg_act_kw"] for r in rows]

            if not plan:
                # 无剧本时环境图才用回放画(有剧本时环境图已是铺满的剧本实线)
                self._ax_env.plot(t, wind, color=theme.CURVE_WIND, lw=1.6,
                                  label="风速")
                self._ax_env.set_ylim(0, max(30, max(wind or [0]) * 1.2))
                self._ax_load.plot(t, load, color=theme.CURVE_LOAD, lw=1.6,
                                   label="负荷")
                self._ax_load.set_ylim(0, max(380, max(load or [0]) * 1.15))
                self._ax_env.legend(loc="upper left", fontsize=9,
                                    framealpha=0.15, labelcolor=theme.TEXT)

            # 功率子图(历史回放; 负荷也改实线 —— 要求"全部实线")
            self._ax_pow.plot(t, load, color=theme.CURVE_LOAD, lw=1.6,
                              label="负荷")
            self._ax_pow.plot(t, wt, color=theme.CURVE_WT, lw=1.8, label="风机")
            self._ax_pow.plot(t, dg, color=theme.CURVE_DG, lw=1.8, label="柴发")
            self._ax_pow.set_ylabel("功率 (kW)", color=theme.SUB, fontsize=10,
                                    labelpad=9)
            self._ax_pow.set_xlabel("仿真时间 (s)", color=theme.SUB, fontsize=10,
                                    labelpad=8)
            self._ax_pow.set_ylim(0, max(380, max(load or [0]) * 1.15))
            self._ax_pow.legend(loc="upper left", fontsize=9, framealpha=0.15,
                                labelcolor=theme.TEXT)
        elif not plan:
            self._ax_env.text(0.5, 0.5, "等待仿真数据…\n(先跑 main.py)",
                              transform=self._ax_env.transAxes,
                              ha="center", va="center",
                              color=theme.SUB, fontsize=13)
            self._canvas.draw_idle()
            return

        # ---------- 居中滚动窗口: "现在"固定在正中央 ----------
        # 窗口 = [now - HALF, now + HALF](已在上面算好); 仿真每推进 1s 窗口右移
        # 1s, 整条曲线看起来一直往左移; "现在"红线永远居中, 不会卡在右端停住。
        self._ax_env.set_xlim(xmin, xmax)
        self._ax_pow.set_xlim(xmin, xmax)
        # 过去区阴影(锁定): 窗口左端 ~ 现在
        self._ax_env.axvspan(xmin, now, color="#9aa7b5", alpha=0.12)
        # "现在"分隔线(居中)
        self._ax_env.axvline(now, color="#E24B4A", ls="--", lw=1.4, alpha=0.9)
        self._ax_env.text(now + 0.5, self._ax_env.get_ylim()[1] * 0.97,
                          "现在", color="#E24B4A", fontsize=9, va="top")

        # 重绘后清掉旧的 edited 列表(marker 已被 cla() 清掉); 拖拽中则重画临时 marker
        self._edited.clear()
        if self._drag is not None:
            self._redraw_drag_marker()
        else:
            self._canvas.draw_idle()