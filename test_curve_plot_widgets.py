from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from curve_plot_widgets import EditableCurvePlot, HistoryPlot
from examples.curve_plot_widgets_demo import CurvePlotDemoWindow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _show(widget, app: QApplication) -> None:
    widget.resize(900, 420)
    widget.show()
    app.processEvents()
    assert not widget.grab().toImage().isNull()


def test_history_plot_has_standalone_multi_curve_api(app):
    plot = HistoryPlot()
    source = {
        "负荷功率": [40.0, 55.0, 48.0, 62.0],
        "风电功率": [12.0, 28.0, 36.0, 31.0],
    }

    plot.set_curves(source)
    _show(plot, app)

    assert plot.curves() == source
    assert list(plot.curves()) == ["负荷功率", "风电功率"]
    assert HistoryPlot.__module__ == "curve_plot_widgets.history_plot"

    # Returned data is a copy and cannot mutate the widget accidentally.
    copied = plot.curves()
    copied["负荷功率"][0] = -999.0
    assert plot.curves()["负荷功率"][0] == 40.0

    plot.close()


def test_editable_plot_mouse_selects_and_edits_nearest_curve(app):
    plot = EditableCurvePlot()
    source = {
        "下限": [20.0, 20.0, 20.0, 20.0, 20.0],
        "上限": [80.0, 80.0, 80.0, 80.0, 80.0],
    }
    plot.set_curves(source)
    _show(plot, app)

    changed: list[tuple[str, int, float]] = []
    finished: list[str] = []
    plot.curvePointChanged.connect(
        lambda name, index, value: changed.append((name, index, value))
    )
    plot.curveEditFinished.connect(finished.append)

    rect = plot.plot_rect()
    minimum, maximum = plot.value_range()
    start = QPoint(
        round(rect.left() + rect.width() * 0.25),
        round(rect.top() + rect.height() * (maximum - 80.0) / (maximum - minimum)),
    )
    end = QPoint(
        round(rect.left() + rect.width() * 0.75),
        round(rect.top() + rect.height() * 0.05),
    )

    QTest.mousePress(plot, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(plot, pos=end, delay=10)
    QTest.mouseRelease(plot, Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()

    assert plot.editable_curve() == "上限"
    assert changed
    assert {name for name, _index, _value in changed} == {"上限"}
    assert finished == ["上限"]
    assert plot.values("下限") == source["下限"]
    assert plot.values("上限") != source["上限"]
    assert plot.curves()["上限"] == plot.values("上限")

    plot.close()


def test_editable_plot_retains_simple_single_curve_compatibility(app):
    plot = EditableCurvePlot()
    plot.set_curve("计划值", [10.0, 20.0, 30.0])
    _show(plot, app)

    assert plot.editable_curve() == "计划值"
    assert plot.values() == [10.0, 20.0, 30.0]

    plot.set_editable_curve("计划值")
    with pytest.raises(KeyError, match="missing"):
        plot.set_editable_curve("missing")

    plot.close()


def test_x_axis_supports_point_numeric_and_time_formats(app):
    plot = HistoryPlot()
    plot.set_curves({"测试曲线": [10.0, 20.0, 30.0, 40.0, 50.0]})
    plot.set_point_axis(5, start=1)
    _show(plot, app)

    rect = plot.plot_rect()
    QTest.mouseMove(plot, pos=QPoint(round(rect.center().x()), round(rect.center().y())))
    app.processEvents()
    assert plot.axis_range() == (1.0, 5.0)
    assert plot.cursor_snapshot()["x_label"] == "点 3"

    plot.set_numeric_axis(0, 100, decimals=1, suffix=" Hz")
    QTest.mouseMove(plot, pos=QPoint(round(rect.center().x()), round(rect.center().y())))
    app.processEvents()
    assert plot.axis_range() == (0.0, 100.0)
    assert plot.cursor_snapshot()["x_label"] == "50.0 Hz"

    plot.set_time_axis(8 * 3600, 12 * 3600, include_seconds=False)
    QTest.mouseMove(plot, pos=QPoint(round(rect.center().x()), round(rect.center().y())))
    app.processEvents()
    assert plot.cursor_snapshot()["x_label"] == "10:00"

    plot.close()


def test_y_axis_supports_auto_fixed_and_one_sided_limits(app):
    plot = HistoryPlot()
    plot.set_curves({"测试曲线": [10.0, 20.0]})
    _show(plot, app)
    assert plot.value_range() == (0.0, 20.0)

    plot.set_y_range(5, 25)
    app.processEvents()
    assert plot.value_range() == (5.0, 25.0)

    plot.set_y_range(minimum=8)
    app.processEvents()
    assert plot.value_range() == (8.0, 20.0)

    plot.set_y_range(maximum=30)
    app.processEvents()
    assert plot.value_range() == (0.0, 30.0)

    plot.reset_y_range()
    app.processEvents()
    assert plot.value_range() == (0.0, 20.0)

    with pytest.raises(ValueError, match="下限必须小于上限"):
        plot.set_y_range(30, 20)

    plot.close()


def test_both_widgets_integrate_into_a_qt_main_window(app):
    window = CurvePlotDemoWindow()
    window.show()
    app.processEvents()

    assert isinstance(window.history_plot, HistoryPlot)
    assert isinstance(window.editable_plot, EditableCurvePlot)
    assert len(window.history_plot.curves()) == 3
    assert len(window.editable_plot.curves()) == 3
    assert not window.grab().toImage().isNull()

    window.editable_plot.curvePointChanged.emit("风电功率 (kW)", 2, 33.25)
    assert "风电功率" in window.edit_status.text()
    assert "33.250" in window.edit_status.text()

    window.close()


def test_html_user_guide_contains_usage_and_integrated_example():
    guide_path = PROJECT_ROOT / "CURVE_PLOT_WIDGETS_USER_GUIDE.html"
    html = guide_path.read_text(encoding="utf-8")

    assert '<html lang="zh-CN">' in html
    assert "HistoryPlot" in html
    assert "EditableCurvePlot" in html
    assert "set_curves" in html
    assert "curvePointChanged" in html
    assert "CurvePlotDemoWindow" in html
    assert "set_point_axis" in html
    assert "set_numeric_axis" in html
    assert "set_y_range" in html
    assert "reset_y_range" in html
    assert 'id="y-axis"' in html
    assert "examples\\curve_plot_widgets_demo.py" in html
    assert "data:image/png;base64," in html
    assert "@media print" in html
