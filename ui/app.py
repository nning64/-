# -*- coding: utf-8 -*-
"""电网模拟器 A 端 · 监视/演示主界面 (PySide6)

运行:
    python ui/app.py                # 开界面, 每秒刷一次 grid.db
    python ui/app.py --selftest     # 起 3.5s 后自动截图存 ui/_selftest.png 退出
说明:
    - 界面直接读正在被 main.py 写的 grid.db(WAL 并发安全), 因此
      演示前先跑 main.py(建议 --comm)让它有数据; 不跑也不崩, 显示等待。
    - 数据通道单向只读; 改参数/下令走 sim_params/device_params 表(黑板书架),
      main 主循环下一帧消费。
    - 功能面板全部是 QDockWidget(仿真控制台/SCADA 实时表/SCADA 历史/运行日志/
      设备参数): 顶部"窗口"菜单可开关; 面板可拖动、浮动、停靠 —— 一个主窗口
      里就能同时开多个面板。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                               QVBoxLayout, QLabel, QSplitter, QStatusBar,
                               QDockWidget)

ROOT = Path(__file__).resolve().parent.parent

# 曲线窗口: 显示最近多少仿真秒。之前是从 self._curve.window_sec 取, 一旦
# matplotlib 还没挂上(self._curve 是 None)整个 _tick 就会抛 AttributeError,
# 连"仿真时刻/RTU 卡片"都一起不刷新了。改成模块常量后, _tick 不再依赖
# self._curve 是否存在。(2026-09-11)
CURVE_WINDOW = 240

# 顶部"秒表"(仿真时刻 t = Ns)的独立轮询周期: 50ms = 20Hz。
# 必须远快于 1Hz, 否则会漏值/错拍 —— 详见 MainWindow._tick_clock。(2026-09-11)
CLOCK_INTERVAL_MS = 50

# ---- matplotlib 冷启动预热 ----
# matplotlib 首次 import + 建 Figure 合计约 0.8s。过去这段在 _mount_curve
# 里于主线程执行, 事件循环被冻住近 1 秒(窗口已经出来了, 但点不动、不重绘),
# 主观感受就是"进了界面非常卡"。修复: app 启动时先起一个后台线程把
# matplotlib 及 QtAgg 后端导入好并建一个热身 Figure; 主线程的 _mount_curve
# 只是"等预热完成 -> 挂 canvas", 不再承担 import 开销。(2026-09-11)
_MPL_READY = threading.Event()
_WARMUP_THREAD = None        # main() 里启动的后台预热线程(供 _mount_curve 判断)


def _warmup_matplotlib():
    """后台线程: 只做 import 与一次热身绘制, 不碰任何 Qt 控件(不能跨线程)。"""
    try:
        import matplotlib
        matplotlib.use("QtAgg")
        from matplotlib.figure import Figure
        Figure(figsize=(2, 2), dpi=72)   # 预热字体度量 / 渲染器
    except Exception:
        pass                              # 失败也别卡住事件: 主线程会自己兜底
    finally:
        _MPL_READY.set()


# 关键路径: matplotlib 冷启动 ~770ms (中文环境更慢),
# 过去在 curve_view.py module-level import 会让 MainWindow __init__ 阻塞
# 等到这一大块才返回, 用户看不到任何东西, 主观感受是"卡死了"。
# 修复: 在 ui/app.py 顶部延迟导入 matplotlib, MainWindow 构造期间完全不
# 接触 matplotlib, 窗口先 show() 显示给用户, 然后下一个事件循环里再异步
# 实例化 CurveView 并替换占位 QLabel。视觉延迟从 ~1.2s 降到 <0.2s。
# 2026-09-11 再加一层: import 本身挪到后台线程(_warmup_matplotlib)。
def _lazy_curve_view(window_sec: int):
    """实例化 CurveView(需要 matplotlib, 已由后台线程预热)。"""
    from ui.widgets.curve_view import CurveView
    return CurveView(window=window_sec)

# 双模式导入: python -m ui.app 走相对导入; python ui/app.py 直接跑也支持
# 注意: curve_view 和 hist_view 含 matplotlib (~770ms 冷启动), 不在顶层 import,
# 改走 _lazy_curve_view() / hist_view dock 第一次显示时再 import, 否则前面
# 写的"窗口先 show 出来"优化就白做了。(2026-09-10)
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
    from ui import db, theme
    from ui.widgets.rtu_view import RtuView
    from ui.widgets.sim_ctrl import SimCtrl
    from ui.widgets.dev_params import DevParams
    from ui.widgets.scada_table import ScadaTable
    from ui.widgets.log_view import LogView
else:
    from . import db, theme
    from .widgets.rtu_view import RtuView
    from .widgets.sim_ctrl import SimCtrl
    from .widgets.dev_params import DevParams
    from .widgets.scada_table import ScadaTable
    from .widgets.log_view import LogView

STATE_TEXT = {0: ("仿真停止", theme.RED), 1: ("仿真运行", theme.GREEN),
              2: ("仿真暂停", theme.AMBER)}
STALL_LIMIT_S = 3.0      # "声称运行但 sim_time 连续不动"多久算主程序没跑


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("南极考察站微电网 · 电网模拟器 A 端监控台")
        self.resize(1500, 920)
        self._last_refresh = "--:--:--"
        self._docks = {}          # 面板名 -> QDockWidget(窗口菜单开关用)
        # 停滞兜底(详见 _tick): 声称运行但 sim_time 不动
        self._last_t = None
        self._last_tick_rt = 0.0
        self._t_stall = 0.0
        self._last_curve_t = None   # 上次画曲线时的 sim_time(避免无谓重绘)
        self._mount_wait = 0        # _mount_curve 等 matplotlib 预热的轮数
        self._chip_key = None       # 顶部状态胶囊当前(文字, 颜色), 变了才重绘
        self._last_status_msg = None  # 状态栏当前文字, 变了才重设(避免每秒闪动)
        self._clock_t = None          # 顶部秒表已显示到哪一秒(只归 _tick_clock 管)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(10, 8, 10, 4)
        v.setSpacing(8)

        v.addWidget(self._build_header())

        split = QSplitter(Qt.Horizontal)
        self._rtu = RtuView()
        # 占位 QLabel: matplotlib 没初始化完之前, 用户能立刻看到 RTU 卡片
        # 和控制台 / SCADA 表, 不会盯着空白主窗口等。下一个事件循环再换。
        self._curve_placeholder = QLabel("环境/功率曲线 · 初始化中…")
        self._curve_placeholder.setAlignment(Qt.AlignCenter)
        self._curve_placeholder.setStyleSheet("color: %s; font-size: 14px;"
                                               % theme.SUB)
        split.addWidget(self._rtu)
        split.addWidget(self._curve_placeholder)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([460, 1000])
        v.addWidget(split, 1)

        # 异步实例化 CurveView (matplotlib 773ms 在这里才发生, 不再阻塞 UI)
        self._curve = None
        QTimer.singleShot(0, self._mount_curve)

        self._build_docks()
        self._build_menus()
        self.statusBar().showMessage("就绪")

        # 每秒刷新(面板/曲线/状态灯这些"重活")
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)   # 关掉 Qt 粗定时器的 ±5% 抖动
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        # 顶部秒表 t = Ns: 单独一条 20Hz 的轻量轮询(只读一个 sim_time)。
        # 不能挂在上面那条 1Hz 上 —— 1 秒才采一次会漏值/错拍, 显示就
        # 既不是匀速也不是一格一格。详见 _tick_clock。(2026-09-11 修)
        self._clock = QTimer(self)
        self._clock.setTimerType(Qt.PreciseTimer)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(CLOCK_INTERVAL_MS)

    # ---------------- 头部 ----------------
    def _build_header(self):
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(2, 0, 2, 0)

        title_col = QVBoxLayout()
        t = QLabel("南极考察站微电网 · 智能调控系统")
        t.setObjectName("title")
        sub = QLabel("学生 A · 电网模拟器 grid_simulator  (数据源 / 协议中枢)")
        sub.setObjectName("sub")
        title_col.addWidget(t)
        title_col.addWidget(sub)
        h.addLayout(title_col)
        h.addStretch(1)

        # 仿真时刻大字
        self._lbl_time = QLabel("t = --s")
        self._lbl_time.setObjectName("big")
        self._lbl_time.setStyleSheet("color: %s;" % theme.CYAN)
        h.addWidget(self._lbl_time)
        h.addSpacing(16)

        # 仿真状态胶囊(EMS/风机控制器通信灯见左栏 R03 卡片, 不在此重复)
        self._chip_state = QLabel("仿真停止")
        self._chip_state.setStyleSheet(theme.chip_style(theme.RED, "#3a1420"))
        h.addWidget(self._chip_state)
        return bar

    def _mount_curve(self):
        """异步挂载 CurveView。

        matplotlib 的 import + Figure 冷启动约 0.8s, 现在由后台线程
        (_warmup_matplotlib) 提前做; 主线程这里只做"等预热完成 -> 把真曲线
        换进 splitter", 不再是"在主线程冻 0.8s"。

        等不到就每 40ms 再看一眼(最多 60 轮 ≈ 2.4s); 若预热线程压根没起
        (比如测试里直接 new MainWindow, 没走 main()) 则不等, 直接自己 import,
        保证曲线一定出得来、且不会白等。
        """
        warming = (_WARMUP_THREAD is not None and _WARMUP_THREAD.is_alive())
        if not _MPL_READY.is_set() and warming and self._mount_wait < 60:
            self._mount_wait += 1
            QTimer.singleShot(40, self._mount_curve)   # 让出事件循环, 稍后再来
            return
        self._mount_wait = 0

        t = time.perf_counter()
        self._curve = _lazy_curve_view(window_sec=CURVE_WINDOW)
        # 把占位换成真曲线, 保留 splitter 比例
        split = self._curve_placeholder.parentWidget()
        idx = split.indexOf(self._curve_placeholder)
        split.replaceWidget(idx, self._curve)
        self._curve_placeholder.deleteLater()
        self._curve_placeholder = None
        # 立刻拿当前库数据补一笔, 不等下一秒定时器
        try:
            self._curve.refresh(db.recent_sim(CURVE_WINDOW))
        except Exception:
            pass
        self._last_curve_t = None    # 让下一帧 _tick 必定重绘一次
        # 拖拽结束 -> 实时反馈到 status bar(成功/越界/历史/暂停 4 种)
        # 之前只画 marker, 用户常常"看不到黄点以外的变化"——其实是信号被吞了
        self._curve.envEdited.connect(self._on_env_edited)
        ms = (time.perf_counter() - t) * 1000
        self.statusBar().showMessage(
            "数据源 grid.db · 曲线已就绪 (%dms) · 最近更新 %s"
            % (int(ms), time.strftime("%H:%M:%S")))
        self._last_status_msg = self.statusBar().currentMessage()

    def _on_env_edited(self, payload):
        """CurveView 拖拽结束的反馈: ('ok'|'warn', msg)。
        ok   -> 绿色 5 秒
        warn -> 红色 8 秒(更醒目, 提示用户操作无效)"""
        kind, msg = payload[0], payload[1]
        if kind == "ok":
            self.statusBar().setStyleSheet("color: #2ecc71;")
            self.statusBar().showMessage(msg, 5000)
        else:
            self.statusBar().setStyleSheet("color: #e74c3c;")
            self.statusBar().showMessage(msg, 8000)
        # _tick 只在"状态栏文字变了"才重设默认消息; 这里把缓存置空, 让下一轮
        # _tick 一定把默认消息(及颜色)盖回来, 否则红/绿提示会一直挂着。(2026-09-11)
        self._last_status_msg = None

    def _ensure_hist(self):
        """历史曲线面板按需实例化: matplotlib 二次冷启动 770ms, 藏到用户
        点 SCADA 历史曲线 tab 时才发生。"""
        if self._hist is not None:
            return
        # 第二次 matplotlib 冷启动已经在 _mount_curve 时发生, 这次 import
        # 实际很快(sys.modules 命中)
        from ui.widgets.hist_view import HistView
        self._hist = HistView()
        self._dock_hist.setWidget(self._hist)
        # HistView.__init__ 里已经取过一次点表, 但那次可能是在历史表还没数据
        # 的时候(比如仿真刚清空)。这里建好 widget 之后再重拉一次点表 + 查询,
        # 保证"跑完一轮才第一次点开这个 tab"时列表不是空的。(2026-09-11)
        try:
            self._hist._reload_points()
            self._hist._query()
        except Exception:
            pass

    def _on_hist_visibility(self, visible):
        """dock 第一次显示时实例化 widget(避开 matplotlib 冷启动)。"""
        if visible:
            self._ensure_hist()

    # ---------------- 功能面板 (QDockWidget) ----------------
    def _mk_dock(self, title: str, widget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setAllowedAreas(Qt.AllDockWidgetAreas)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        self._docks[title] = d
        return d

    def _build_docks(self):
        # 底部 tab 组: SCADA 实时表(默认前置) / 运行日志 / 设备参数 / 历史曲线
        self._scada = ScadaTable()
        self._log   = LogView()
        self._dev   = DevParams()
        # 历史曲线: 第一次切到 tab 时才实例化 (matplotlib 二次冷启动 770ms)
        self._hist  = None
        dock_scada = self._mk_dock("SCADA 实时表", self._scada,
                                   Qt.BottomDockWidgetArea)
        dock_log = self._mk_dock("运行日志", self._log,
                                 Qt.BottomDockWidgetArea)
        dock_dev = self._mk_dock("设备参数", self._dev,
                                 Qt.BottomDockWidgetArea)
        self._dock_hist = QDockWidget("SCADA 历史曲线", self)
        self._dock_hist.setObjectName("SCADA 历史曲线")
        self._dock_hist.setAllowedAreas(Qt.AllDockWidgetAreas)
        self._dock_hist.visibilityChanged.connect(self._on_hist_visibility)
        self.addDockWidget(Qt.BottomDockWidgetArea, self._dock_hist)
        self._docks["SCADA 历史曲线"] = self._dock_hist
        for d in (dock_log, dock_dev, self._dock_hist):
            self.tabifyDockWidget(dock_scada, d)
        dock_scada.raise_()

        # 右侧独立: 仿真控制台(与底部 tab 组是两块不同停靠区)
        self._sim = SimCtrl()
        dock_sim = self._mk_dock("仿真控制台", self._sim,
                                 Qt.RightDockWidgetArea)

        # "做变更让别的都跟着动" —— 控制台按钮按下后, 立即联动所有面板,
        # 不必等 1 秒定时器。信号名: 'start'/'pause'/'resume'/'stop'/'apply'
        self._sim.command_issued.connect(self._on_ctrl_issued)

        # 初始尺寸: 底部带 250 高, 右侧栏 340 宽
        QTimer.singleShot(0, lambda: self._apply_dock_sizes(
            dock_scada, dock_sim))

    def _apply_dock_sizes(self, dock_bottom, dock_right):
        try:
            self.resizeDocks([dock_bottom], [250], Qt.Vertical)
            self.resizeDocks([dock_right], [340], Qt.Horizontal)
        except Exception:
            pass

    # ---------------- 窗口菜单 ----------------
    def _build_menus(self):
        m = self.menuBar().addMenu("窗口")
        for title, dock in self._docks.items():
            a = QAction(title, self)
            a.setCheckable(True)
            a.setChecked(dock.isVisible())
            dock.visibilityChanged.connect(a.setChecked)
            a.toggled.connect(lambda on, d=dock: self._toggle_dock(d, on))
            m.addAction(a)

    @staticmethod
    def _toggle_dock(dock: QDockWidget, on: bool):
        """开则显示并提到最前(tab 组内也切过去); 关则隐藏。"""
        dock.setVisible(on)
        if on:
            dock.raise_()

    def _on_ctrl_issued(self, cmd: str):
        """SimCtrl 下发命令后, 立即联动所有面板重读库(不等 1 秒定时器)。

        关键修复: 用户改启动时间点"启动"后, 视觉上立刻看到曲线/SCADA
        表/日志全部同步清空+回填, 而不是最坏 1 秒后才看到(像"卡住")。
        """
        # 曲线: 历史已清 -> 立刻看到"等待仿真数据…"
        # (曲线可能在 matplotlib 冷启动 773ms 期间还未挂上, 容忍 None)
        if self._curve is not None:
            try:
                self._curve.refresh(db.recent_sim(CURVE_WINDOW))
            except Exception:
                pass
        # 底部四个面板: 各 _reload 一次
        for w in (self._scada, self._log, self._dev):
            try:
                w._reload()
            except Exception:
                pass
        # 历史曲线面板: 仅当已实例化时才重画(没切过 tab, _hist 还是 None)
        if self._hist is not None:
            try:
                self._hist._query()
            except Exception:
                pass

    # ---------------- 刷新 ----------------
    def _tick_clock(self):
        """只刷顶部秒表 t = Ns。独立一条 20Hz 轮询, 不跟 1Hz 的 _tick 混在一起。

        为什么必须分开(本次"秒表乱跳"的根因):
          main.py 的时钟是"每 1.000s 精确 +1"(默认 speed=1 / step=1);
          而 _tick 也是每 1.000s 才读一次库。两个各自 1 秒的周期必然互相
          错拍(拍频): 采样点有时落在 main 写库之前、有时落在之后, 于是顶部
          数字会 +1 / +0 / +2 混着跳 —— 既不是匀速, 也不是一格一格。
          把"只读一个 sim_time"的轮询提到 20Hz 后, 每个新值都必然被看到、
          且只显示一次, 显示节奏就等于数据节奏: 1x 每秒 +1, 3x 每 0.33s +1,
          且永不跳号。成本实测约 0.2ms/次(开连接+读一个 key), 可忽略。
        """
        try:
            t = int(db.sim_params_all().get("sim_time", 0) or 0)
        except Exception:
            return                     # 库被锁/还没建: 保持上一次显示
        if t != self._clock_t:
            self._clock_t = t
            self._lbl_time.setText("t = %ds" % t)

    def _tick(self):
        try:
            info = db.sim_info()
            yc = db.yc_values()
            yx = db.yx_values()
            rows = db.recent_sim(CURVE_WINDOW)
        except Exception as e:                       # 库被锁/还没建
            self.statusBar().setStyleSheet("")
            self.statusBar().showMessage("读库失败: %s" % e)
            self._last_status_msg = None
            return

        try:
            t = int(info.get("sim_time", 0) or 0)
        except (TypeError, ValueError):
            t = 0

        # ---- 仿真状态: 直接读 sim_params.sim_state(与右侧仿真控制台同源) ----
        # 旧实现读 yx_realtime 的 R03:1(SIM_RUN), 而 main.py 过去只在"运行帧"
        # 才写那个点: 一旦暂停/停止它就永远停在 1, 顶部胶囊一直亮绿"仿真运行",
        # 而 t 和曲线全冻住 —— 这正是"终端在跑、界面不动、感觉卡死"的主因。
        # main.py 现已改成三态都同步 R03:1(见 main.py 第 0.5 步); 这里再直接
        # 以最权威的 sim_state 为准, 双重保险。(2026-09-11 修)
        try:
            st = int(info.get("sim_state", 0) or 0)
        except (TypeError, ValueError):
            st = 0

        # ---- 停滞兜底 ----
        # 状态说"运行", 但 sim_time 连续 STALL_LIMIT_S 没动过 -> 主程序大概率
        # 没在跑(或卡住了)。此时把胶囊翻成红色提示, 而不是继续假装正常。
        now_rt = time.time()
        t_changed = (self._last_t is None or t != self._last_t)
        if t_changed:
            self._last_t, self._last_tick_rt = t, now_rt
            self._t_stall = 0.0
        else:
            self._t_stall = now_rt - self._last_tick_rt
        stalled = (st == 1 and self._t_stall >= STALL_LIMIT_S)

        # 注: 顶部"秒表" t = Ns 不在这里刷 —— 它由 _tick_clock 以 20Hz 单独
        # 驱动(1Hz 采样会漏值/错拍)。下面只负责状态胶囊 / 面板 / 曲线。

        # 状态胶囊: 只在(文字, 颜色)真的变了才重绘 —— 否则每秒 setStyleSheet
        # 会让整条 header 重排一次
        text, col = STATE_TEXT.get(st, STATE_TEXT[0])
        if stalled:
            text, col = "运行? 主程序没跑", theme.RED
        if (text, col) != self._chip_key:
            self._chip_key = (text, col)
            self._chip_state.setText(text)
            self._chip_state.setStyleSheet(theme.chip_style(
                col, "#2a2438" if (st == 1 and not stalled) else "#3a1420"))

        self._rtu.refresh(yc, {k: int(v) for k, v in yx.items()})

        # 曲线: matplotlib 还没挂上(仍为 None)时跳过; sim_time 没变说明没有新
        # 样本, 也不必重绘(refresh 内部 cla + 全量重画 ≈ 33ms, 每秒白烧一次)
        if self._curve is not None and t != self._last_curve_t:
            try:
                self._curve.refresh(rows)
                self._last_curve_t = t
            except Exception:
                pass

        # 状态栏: 文字没变就不重设(每秒 showMessage/setStyleSheet 会引起状态栏
        # 重排闪烁)。停滞时把告警顶上去。
        self._last_refresh = time.strftime("%H:%M:%S")
        msg = "数据源 grid.db · 每秒刷新 · 最近更新 %s" % self._last_refresh
        if stalled:
            msg = ("⚠ 状态=运行 但 t 已 %ds 未变化 · 请确认 main.py 是否在跑"
                   % int(self._t_stall))
        if msg != self._last_status_msg:
            self._last_status_msg = msg
            self.statusBar().setStyleSheet("")     # 清掉拖拽反馈留下的红/绿
            self.statusBar().showMessage(msg)

    # ---------------- 自检截图 ----------------
    def save_screenshot(self, path=None):
        path = path or (ROOT / "ui" / "_selftest.png")
        ok = self.grab().save(str(path))
        print("SELFTEST_SAVED" if ok else "SELFTEST_FAIL", path)


def main():
    global _WARMUP_THREAD
    app = QApplication(sys.argv)
    app.setApplicationName("grid_sim_ui")
    app.setFont(QFont(theme.FONT_FAMILY, 10))
    app.setStyleSheet(theme.QSS)

    # 后台预热 matplotlib(import + Figure ≈ 0.8s): 与"建窗口 / 摆控件 / 建 dock"
    # 并行跑, 等 _mount_curve 触发时通常已经好了, 主线程不再被 import 冻住。
    # 必须放在 MainWindow 之前, 这样预热期能完全被窗口构造时间盖住。(2026-09-11)
    _WARMUP_THREAD = threading.Thread(target=_warmup_matplotlib,
                                      name="mpl-warmup", daemon=True)
    _WARMUP_THREAD.start()

    win = MainWindow()
    win.show()

    if "--selftest" in sys.argv:
        # 给 4 秒让数据刷两轮再截图, 便于检查渲染
        QTimer.singleShot(4000, lambda: (win.save_screenshot(), app.quit()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
