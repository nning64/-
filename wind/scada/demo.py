"""最小可运行示例：手绘风速和太阳辐照曲线。"""

from __future__ import annotations

import math
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from curve_data import CurveConfig, CurveData, load_json, save_csv, save_json
from curve_widget import CurveEditor


def default_curves() -> tuple[CurveData, CurveData]:
    wind_config = CurveConfig(
        "wind_speed", "风速输入", "m/s", 0.0, 30.0, "#2f80ed"
    )
    solar_config = CurveConfig(
        "solar_irradiance", "太阳辐照输入", "W/m²", 0.0, 1200.0, "#f2994a"
    )
    wind = CurveData.from_function(
        wind_config,
        lambda index, _seconds: 8.5
        + 2.2 * math.sin(2 * math.pi * (index - 180) / 1440),
    )

    def sunlight(index: int, _seconds: float) -> float:
        if not 360 <= index <= 1080:
            return 0.0
        return 900.0 * math.sin(math.pi * (index - 360) / 720)

    return wind, CurveData.from_function(solar_config, sunlight)


class DemoWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Qt 手绘实时输入曲线示例")
        self.resize(1050, 750)
        wind, solar = default_curves()
        self.wind_editor = CurveEditor(wind)
        self.solar_editor = CurveEditor(solar)
        self._build_ui()
        self._connect_signals()
        self.update_preview()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("按住鼠标左键在曲线区拖动，修改结果会立即刷新")
        title.setStyleSheet("font-size: 18px; font-weight: 600; padding: 6px;")
        layout.addWidget(title)
        layout.addWidget(self.wind_editor, 1)
        layout.addWidget(self.solar_editor, 1)

        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 1440)
        layout.addWidget(self.time_slider)
        self.preview = QLabel()
        self.preview.setStyleSheet("font-size: 16px; padding: 8px;")
        layout.addWidget(self.preview)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("导出 JSON")
        self.load_button = QPushButton("加载 JSON")
        self.csv_button = QPushButton("导出 CSV")
        for button in (self.save_button, self.load_button, self.csv_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def _connect_signals(self) -> None:
        self.time_slider.valueChanged.connect(self.update_preview)
        self.wind_editor.curveChanged.connect(self.update_preview)
        self.solar_editor.curveChanged.connect(self.update_preview)
        self.save_button.clicked.connect(self.export_json)
        self.load_button.clicked.connect(self.import_json)
        self.csv_button.clicked.connect(self.export_csv)

    def curves(self) -> list[CurveData]:
        return [self.wind_editor.curve, self.solar_editor.curve]

    def update_preview(self, *_unused) -> None:
        minute = self.time_slider.value()
        seconds = minute * 60
        self.wind_editor.set_playhead(seconds)
        self.solar_editor.set_playhead(seconds)
        time_text = "24:00" if minute == 1440 else f"{minute // 60:02d}:{minute % 60:02d}"
        self.preview.setText(
            f"当前时刻 {time_text}    "
            f"风速 {self.wind_editor.value_at(seconds):.3f} m/s    "
            f"太阳辐照 {self.solar_editor.value_at(seconds):.3f} W/m²"
        )

    def export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出", "curves.json", "JSON (*.json)")
        if path:
            save_json(path, self.curves())

    def import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载", "", "JSON (*.json)")
        if not path:
            return
        try:
            curves = {curve.config.key: curve for curve in load_json(path)}
            self.wind_editor.set_curve(curves["wind_speed"])
            self.solar_editor.set_curve(curves["solar_irradiance"])
            self.update_preview()
        except (KeyError, OSError, ValueError) as error:
            QMessageBox.critical(self, "加载失败", str(error))

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出", "curves.csv", "CSV (*.csv)")
        if path:
            save_csv(path, self.curves())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DemoWindow()
    window.show()
    raise SystemExit(app.exec())

