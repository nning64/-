# -*- coding: utf-8 -*-
"""仿真控制台: 显示/修改 仿真起始时刻、结束时刻、步长; 控制 启动/暂停/继续/停止。

注意: 本控件(UI 进程)只往 sim_params 表写控制命令(ctrl_cmd)与参数,
主循环(main.py)每帧读并消费 —— 两个进程通过 grid.db 这面'黑板'协作。
因此必须先跑 main.py, 控制台才有人'执行'; main 没跑时发命令不会生效。

联动设计(2026-09-08 三次加固):
- 用户改过的 spin 值打"脏标记", 刷新时绝不回写覆盖 —— 只有库里值变化
  且本地无编辑时才同步显示。改完不点按钮, 数字也会一直留在那里。
- start 时 UI 立刻清空历史表(sim_history/yc_history/yx_history),
  曲线/SCADA 表/历史曲线面板立即变"等待…", 视觉反馈即时; main 接
  start 命令下一帧开始写新数据, 曲线自然跟着动。
- start 时 sim_start_s 原样下发(不再按场景周期折回): 主循环
  env_curve_lookup 内部自动取模, 任意起始 sim_time 都合法。
- 每次按钮按下后通过 command_issued 信号通知其他面板立即刷新,
  不必等定时器(按下到看到曲线动之间最多 100ms 而非 1s)。
- 命令发出 3 秒仍未消费 -> 状态变红"主程序未跑"。
- 停滞检测"运行? 主程序未跑" = sim_state=1 但 sim_time 连续 >3s 不变。
  配合 main 固定每秒推进, 正常不该触发; 只有 main 真死了才红。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QGridLayout, QHBoxLayout, QVBoxLayout,
                               QLabel, QSpinBox, QPushButton, QAbstractSpinBox)

from .. import db, theme

_ST = {0: ("停止", theme.RED), 1: ("运行", theme.GREEN), 2: ("暂停", theme.AMBER)}


class SimCtrl(QWidget):
    # 下发控制命令后通知外面(参数=命令名 'start'/'pause'/'resume'/'stop'/'apply')
    command_issued = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_t = None      # 上次刷新读到的 sim_time(停滞检测用)
        self._last_rt = 0.0
        self._stall = 0.0        # sim_state=1 但 sim_time 连续不动多久(秒)
        self._dirty = set()      # 用户改过、尚未写库的 spin 集合
        self._sync = False       # 程序自己 setValue 时置 True, 不算用户编辑
        self._cmd_since = None   # ctrl_cmd 发出后未被消费的起始时刻(秒)
        self._last_start = 0.0   # 上次"启动"按下时刻(秒) —— 连点防抖
        self._clock_t = None     # 秒表已显示到哪一秒(只归 _tick_clock 管)
        self._build()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        # 秒表(当前时刻)单独 20Hz 刷新: 挂在上面那条 1Hz 上会和 main 的
        # 每秒推进错拍, 显示成 +1/+0/+2 乱跳。详见 _tick_clock。
        self._clock = QTimer(self)
        self._clock.setTimerType(Qt.PreciseTimer)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(50)
        self._refresh()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 状态行: 当前时刻 + 状态胶囊
        st = QHBoxLayout()
        self._lbl_time = QLabel("t = --s")
        self._lbl_time.setFont(QFont("Consolas", 16, QFont.Bold))
        self._lbl_time.setStyleSheet("color: %s;" % theme.CYAN)
        self._chip = QLabel("--")
        st.addWidget(self._lbl_time)
        st.addSpacing(12)
        st.addWidget(self._chip)
        st.addStretch(1)
        root.addLayout(st)

        # 参数区
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._sp_start = self._mk_spin(0, 999999, " s")
        self._sp_end = self._mk_spin(0, 999999, " s")
        self._sp_step = self._mk_spin(1, 60, " s")
        for sp, key in ((self._sp_start, "sim_start_s"),
                        (self._sp_end, "sim_end_s"),
                        (self._sp_step, "sim_step_s")):
            sp.valueChanged.connect(lambda _v, k=key: self._on_edit(k))
        grid.addWidget(QLabel("起始时刻"), 0, 0)
        grid.addWidget(self._sp_start, 0, 1)
        grid.addWidget(QLabel("结束时刻"), 1, 0)
        grid.addWidget(self._sp_end, 1, 1)
        grid.addWidget(QLabel("步长"), 2, 0)
        grid.addWidget(self._sp_step, 2, 1)
        self._sp_end.setSpecialValueText("0 = 不限")
        root.addLayout(grid)

        # 控制按钮
        btns = QHBoxLayout()
        self._btn_start = QPushButton("启动")
        self._btn_pause = QPushButton("暂停")
        self._btn_resume = QPushButton("继续")
        self._btn_stop = QPushButton("停止")
        self._btn_stop.setObjectName("danger")
        self._btn_apply = QPushButton("应用参数")
        for b in (self._btn_start, self._btn_pause, self._btn_resume,
                  self._btn_stop, self._btn_apply):
            btns.addWidget(b)
        root.addLayout(btns)

        # 操作反馈行: 每次点按钮后告诉用户"已做什么"
        self._lbl_msg = QLabel("")
        self._lbl_msg.setObjectName("sub")
        self._lbl_msg.setWordWrap(True)
        root.addWidget(self._lbl_msg)

        # 提示
        tip = QLabel(
            "提示: 命令经 grid.db 由 main.py 消费 —— 请先跑 main.py。\n"
            "红色\"主程序未跑\" = 命令发出 3 秒无人执行。改代码后要重开窗口才生效。")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        root.addWidget(tip)
        root.addStretch(1)

        # 信号
        self._btn_start.clicked.connect(self._do_start)
        self._btn_pause.clicked.connect(lambda: self._do_cmd("pause", "已下发: 暂停"))
        self._btn_resume.clicked.connect(lambda: self._do_cmd("resume", "已下发: 继续"))
        self._btn_stop.clicked.connect(lambda: self._do_cmd("stop", "已下发: 停止"))
        self._btn_apply.clicked.connect(self._apply)

    @staticmethod
    def _mk_spin(lo, hi, suffix):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSuffix(suffix)
        # 去掉右侧上下箭头(2026-09-08): 用户嫌箭头占地方且易误触,
        # 输入框保留直接键盘输入即可, 界面更简洁。
        sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
        sp.setAccelerated(True)   # 长按/连按键盘上下键快速加速增减
        return sp

    # ---------------- 用户编辑 -> 脏标记 ----------------
    def _on_edit(self, key):
        if not self._sync:
            self._dirty.add(key)

    def _do_cmd(self, cmd, msg):
        db.send_ctrl(cmd)
        self._say(msg)
        self.command_issued.emit(cmd)        # 通知其他面板立刻刷新

    def _do_start(self):
        # 0) 连点防抖(0.6s): 早前 UI 反应慢时容易连点, 每次点击 main 都会
        #    清历史+重置+打印一条"[+CTRL] 启动", 工作台刷屏、观感很差。
        #    距上次启动 <0.6s 的点击直接忽略, 只提示一次。(2026-09-08)
        _now = time.time()
        if _now - self._last_start < 0.6:
            self._say("启动已触发, 请稍候…(防连点)")
            return
        self._last_start = _now

        # 0) 起始时刻原样下发, 不折回: 主循环按 env_curve 周期自动取模
        #    (loop 模式), 用户想从任意 sim_time 起步都合法, 不再被钳位。
        #    (早期版本会把 start >= period 折回, 导致滑块超过周期上限就
        #     "只能调到 period-1", 现已移除该限制。)
        start = self._sp_start.value()

        # 1) UI 自己立刻清空历史表(不等 main): 曲线/SCADA/历史面板
        #    立即变空, 视觉反馈即时; main 接 start 后下一帧开始写新数据
        n = db.clear_history()
        clear_note = "历史已清(sim=%d yc=%d yx=%d 行)" % (
            n["sim_history"], n["yc_history"], n["yx_history"])

        # 2) 写参数(起始/结束/步长当前显示值写库)
        self._apply(quiet=True)

        # 3) 下发 start 命令
        db.send_ctrl("start")
        self._say("已下发: 启动 (起始=%ds, 结束=%ds, 步长=%ds) %s"
                  % (self._sp_start.value(), self._sp_end.value(),
                     self._sp_step.value(), clear_note))
        self.command_issued.emit("start")

    def _apply(self, quiet=False):
        """把起始/结束/步长写进 sim_params(main 下帧起生效)。"""
        db.set_sim_param("sim_start_s", self._sp_start.value())
        db.set_sim_param("sim_end_s", self._sp_end.value())
        db.set_sim_param("sim_step_s", self._sp_step.value())
        self._dirty.clear()
        if not quiet:
            self._say("参数已写库: 起=%ds 终=%ds 步=%ds (main 下一帧生效)"
                      % (self._sp_start.value(), self._sp_end.value(),
                         self._sp_step.value()))
        self.command_issued.emit("apply")

    def _say(self, text):
        self._lbl_msg.setText("%s  [%s]"
                              % (text, time.strftime("%H:%M:%S")))

    def _tick_clock(self):
        """只刷"当前时刻"秒表 t = Ns(20Hz)。

        main.py 每 1.000s 精确 +1, 而 _refresh 也是每 1.000s 才读一次库,
        两个同频周期互相错拍 -> 显示会 +1/+0/+2 乱跳(既不是匀速, 也不是
        一格一格)。这里用 20Hz 只读一个 sim_time, 保证每个新值恰好显示
        一次、且不跳号。
        """
        try:
            t = int(db.sim_params_all().get("sim_time", 0) or 0)
        except Exception:
            return
        if t != self._clock_t:
            self._clock_t = t
            self._lbl_time.setText("t = %ds" % t)

    def _refresh(self):
        try:
            p = db.sim_params_all()
            t = int(p.get("sim_time", 0) or 0)
            st = int(p.get("sim_state", 0) or 0)
            cmd = p.get("ctrl_cmd", "") or ""
            # 回写显示: 只同步"用户没改过"的 spin; 用户改过的(脏)绝不覆盖,
            # 直到点了 启动/应用参数 写库后才恢复跟随库里值。
            self._sync = True
            for sp, key, dft in ((self._sp_start, "sim_start_s", 0),
                                 (self._sp_end, "sim_end_s", 0),
                                 (self._sp_step, "sim_step_s", 1)):
                if key not in self._dirty:
                    sp.setValue(int(p.get(key, dft) or dft))
            self._sync = False
        except Exception:
            return

        # ---- 检测 1: 命令发出去了没人消费(main 没跑的铁证) ----
        now = time.time()
        if cmd:
            if self._cmd_since is None:
                self._cmd_since = now
        else:
            self._cmd_since = None
        no_consumer = self._cmd_since is not None and (now - self._cmd_since) > 3

        # ---- 检测 2: state=运行 但 sim_time 连续不动 ----
        if self._last_t is not None and st == 1:
            if t == self._last_t:
                self._stall += now - self._last_rt
            else:
                self._stall = 0.0
        self._last_t, self._last_rt = t, now

        # 注: 秒表 t = Ns 不在这里刷 —— 由 _tick_clock 以 20Hz 单独驱动
        # (1Hz 采样会漏值/错拍)。这里只负责下面的停滞判断和状态胶囊。
        text, col = _ST.get(st, _ST[0])
        if no_consumer:
            text, col = "主程序未跑!", theme.RED
        elif st == 1 and self._stall > 3:
            text, col = "运行? 主程序未跑", theme.RED
        self._chip.setText(text)
        self._chip.setStyleSheet(theme.chip_style(
            col, "#2a2438" if col == theme.GREEN else "#3a1420"))