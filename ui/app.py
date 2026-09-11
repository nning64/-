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
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                               QVBoxLayout, QLabel, QSplitter, QStatusBar,
                               QDockWidget)

ROOT = Path(__file__).resolve().parent.parent

# 关键路径: matplotlib 冷启动 ~770ms (中文环境更慢),
# 过去在 curve_view.py module-level import 会让 MainWindow __init__ 阻塞
# 等到这一大块才返回, 用户看不到任何东西, 主观感受是"卡死了"。
# 修复: 在 ui/app.py 顶部延迟导入 matplotlib, MainWindow 构造期间完全不
# 接触 matplotlib, 窗口先 show() 显示给用户, 然后下一个事件循环里再异步
# 实例化 CurveView 并替换占位 QLabel。视觉延迟从 ~1.2s 降到 <0.2s。
# (2026-09-10 修)
def _lazy_curve_view(window_sec: int):
    """第一次调用时 import matplotlib + 实例化 CurveView。"""
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("南极考察站微电网 · 电网模拟器 A 端监控台")
        self.resize(1500, 920)
        self._last_refresh = "--:--:--"
        self._docks = {}          # 面板名 -> QDockWidget(窗口菜单开关用)

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

        # 每秒刷新
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

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
        """异步挂载 CurveView。matplotlib 773ms 冷启动在这里发生, 但 UI
        早已 visible, 用户看到的是"占位符 → 真实曲线"的平滑过渡。
        """
        t = time.perf_counter()
        self._curve = _lazy_curve_view(window_sec=240)
        # 把占位换成真曲线, 保留 splitter 比例
        split = self._curve_placeholder.parentWidget()
        idx = split.indexOf(self._curve_placeholder)
        split.replaceWidget(idx, self._curve)
        self._curve_placeholder.deleteLater()
        self._curve_placeholder = None
        # 立刻拿当前库数据补一笔, 不等下一秒定时器
        try:
            self._curve.refresh(db.recent_sim(self._curve.window_sec))
        except Exception:
            pass
        # 拖拽结束 -> 实时反馈到 status bar(成功/越界/历史/暂停 4 种)
        # 之前只画 marker, 用户常常"看不到黄点以外的变化"——其实是信号被吞了
        self._curve.envEdited.connect(self._on_env_edited)
        ms = (time.perf_counter() - t) * 1000
        self.statusBar().showMessage(
            "数据源 grid.db · 曲线已就绪 (%dms) · 最近更新 %s"
            % (int(ms), time.strftime("%H:%M:%S")))

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
        # 下一轮 _tick 时 statusBar 会刷成默认"每秒刷新"消息, 把颜色盖回去

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
        try:
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
                self._curve.refresh(db.recent_sim(self._curve.window_sec))
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
    def _tick(self):
        try:
            info = db.sim_info()
            yc = db.yc_values()
            yx = db.yx_values()
            rows = db.recent_sim(self._curve.window_sec)
        except Exception as e:                       # 库被锁/还没建
            self.statusBar().showMessage("读库失败: %s" % e)
            return

        try:
            t = int(info.get("sim_time", 0))
        except ValueError:
            t = 0
        self._lbl_time.setText("t = %ds" % t)

        # 仿真状态: 与四遥表同源, 看 yx 表 R03:1 (SIM_RUN) 而非 sim_params
        yx = {k: int(v) for k, v in yx.items()}
        st = yx.get(("R03", 1), 0)
        text, col = STATE_TEXT.get(st, STATE_TEXT[0])
        self._chip_state.setText(text)
        self._chip_state.setStyleSheet(theme.chip_style(
            col, "#2a2438" if st == 1 else "#3a1420"))

        self._rtu.refresh(yc, yx)
        # 曲线 matplotlib 冷启动期间 self._curve 可能还是 None, 容忍
        if self._curve is not None:
            self._curve.refresh(rows)

        self._last_refresh = time.strftime("%H:%M:%S")
        self.statusBar().showMessage(
            "数据源 grid.db · 每秒刷新 · 最近更新 %s" % self._last_refresh)

    # ---------------- 自检截图 ----------------
    def save_screenshot(self, path=None):
        path = path or (ROOT / "ui" / "_selftest.png")
        ok = self.grab().save(str(path))
        print("SELFTEST_SAVED" if ok else "SELFTEST_FAIL", path)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("grid_sim_ui")
    app.setFont(QFont(theme.FONT_FAMILY, 10))
    app.setStyleSheet(theme.QSS)

    win = MainWindow()
    win.show()

    if "--selftest" in sys.argv:
        # 给 4 秒让数据刷两轮再截图, 便于检查渲染
        QTimer.singleShot(4000, lambda: (win.save_screenshot(), app.quit()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
