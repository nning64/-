"""Integrate both reusable curve widgets into a normal Qt main window."""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QLabel,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from curve_plot_widgets import EditableCurvePlot, HistoryPlot  # noqa: E402


def _demo_curves() -> dict[str, list[float]]:
    points = range(25)
    return {
        "负荷功率 (kW)": [55 + 12 * math.sin(index * math.pi / 12) for index in points],
        "风电功率 (kW)": [30 + 15 * math.sin(index * math.pi / 8) for index in points],
        "光伏功率 (kW)": [max(0.0, 45 * math.sin((index - 6) * math.pi / 12)) for index in points],
    }


class CurvePlotDemoWindow(QMainWindow):
    """A complete integration example that can be copied into another Qt app."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HistoryPlot / EditableCurvePlot 集成示例")
        self.resize(1100, 820)

        self.modify_value = {}

        central = QWidget(self)
        page_layout = QVBoxLayout(central)
        page_layout.setContentsMargins(12, 12, 12, 12)
        page_layout.setSpacing(8)

        intro = QLabel(
            "上图用于多曲线只读展示；下图按住鼠标左键拖动，控件会自动选择离鼠标最近的曲线并修改。",
            central,
        )
        intro.setWordWrap(True)
        page_layout.addWidget(intro)

        splitter = QSplitter(Qt.Orientation.Vertical, central)
        page_layout.addWidget(splitter, 1)

        history_group = QGroupBox("HistoryPlot：多曲线展示", splitter)
        history_layout = QVBoxLayout(history_group)
        self.history_plot = HistoryPlot(history_group)
        # 最简调用：传入“曲线名称 -> 数值序列”的字典。
        self.history_plot.set_curves(_demo_curves())
        history_layout.addWidget(self.history_plot)
        splitter.addWidget(history_group)

        editable_group = QGroupBox("EditableCurvePlot：多曲线鼠标编辑", splitter)
        editable_layout = QVBoxLayout(editable_group)
        self.edit_status = QLabel("尚未修改；请在任意曲线附近按住鼠标左键拖动。", editable_group)
        self.editable_plot = EditableCurvePlot(editable_group)
        # EditableCurvePlot 与 HistoryPlot 使用完全相同的 set_curves 接口。
        self.editable_plot.set_curves(_demo_curves())
        self.editable_plot.curvePointChanged.connect(self._show_changed_point)
        self.editable_plot.curveEditFinished.connect(self._show_finished_curve)
        editable_layout.addWidget(self.edit_status)
        editable_layout.addWidget(self.editable_plot, 1)
        splitter.addWidget(editable_group)
        splitter.setSizes([360, 360])

        self.setCentralWidget(central)


    def _show_changed_point(self, name: str, index: int, value: float) -> None:
        self.edit_status.setText(
            f"正在修改：{name}，点 {index + 1}，当前值 {value:.3f}"
        )
        if name not in self.modify_value:
            self.modify_value[name] = {}
        self.modify_value[name][index] = value

    def _show_finished_curve(self, name: str) -> None:
        values = self.editable_plot.values(name)
        self.edit_status.setText(
            f"修改完成：{name}，共 {len(values)} 个点；可调用 values({name!r}, modifyvalue:{len(self.modify_value)})"
        )
        print(self.modify_value)
        self.modify_value.clear()


def main() -> int:
    app = QApplication(sys.argv)
    window = CurvePlotDemoWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
