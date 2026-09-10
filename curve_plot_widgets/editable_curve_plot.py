"""Mouse-editable multi-curve widget."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget

from .history_plot import SECONDS_PER_DAY, HistoryPlot


class EditableCurvePlot(HistoryPlot):
    """Display multiple curves and edit the nearest one with the mouse.

    Pressing the left mouse button selects the curve nearest to the pointer.
    Dragging then paints values continuously; skipped points are interpolated.
    """

    # Compatibility signals used by the original one-curve widget.
    pointChanged = pyqtSignal(int, float)
    editFinished = pyqtSignal()
    pointHovered = pyqtSignal(int, float)

    # Named signals are convenient when multiple curves are supplied.
    curvePointChanged = pyqtSignal(str, int, float)
    curveEditFinished = pyqtSignal(str)
    curvePointHovered = pyqtSignal(str, int, float)
    editableCurveChanged = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._editable_curve = ""
        self._edit_value_range = (0.0, 100.0)
        self._dragging = False
        self._last_drag_point: tuple[int, float] | None = None
        self._active_index: int | None = None
        self.set_time_axis(0, SECONDS_PER_DAY, include_seconds=False)

    def set_curves(
        self,
        curves: Mapping[str, Sequence[float]],
        *,
        x_values: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
        editable_curve: str | None = None,
    ) -> None:
        """Replace the curves and optionally choose the initial editable one."""

        previous = self._editable_curve
        super().set_curves(curves, x_values=x_values)
        if editable_curve is not None:
            requested = str(editable_curve)
            if requested not in self._series:
                raise KeyError(requested)
            selected = requested
        elif previous in self._series:
            selected = previous
        else:
            selected = next(iter(self._series), "")
        self._editable_curve = selected
        all_values = [value for values in self._series.values() for value in values]
        self._edit_value_range = self._suggest_value_range(all_values)
        self._active_index = None
        self._dragging = False
        self._last_drag_point = None
        if selected != previous:
            self.editableCurveChanged.emit(selected)
        self.update()

    def set_curve(
        self,
        name: str,
        values: Sequence[float],
        *,
        x_values: Sequence[float] | None = None,
    ) -> None:
        """Convenience wrapper for the common single-curve case."""

        curve_name = str(name)
        value_list = list(values)
        coordinates = (
            list(x_values)
            if x_values is not None
            else [index * 60 for index in range(len(value_list))]
        )
        self.set_curves(
            {curve_name: value_list} if value_list else {},
            x_values=coordinates if value_list else None,
            editable_curve=curve_name if value_list else None,
        )

    def set_editable_curve(self, name: str) -> None:
        """Select a curve for editing; mouse presses also do this automatically."""

        curve_name = str(name)
        if curve_name not in self._series:
            raise KeyError(curve_name)
        if curve_name == self._editable_curve:
            return
        self._editable_curve = curve_name
        self._active_index = None
        self._last_drag_point = None
        self.editableCurveChanged.emit(curve_name)
        self.update()

    def editable_curve(self) -> str:
        return self._editable_curve

    def set_time_window(self, start: int | float, end: int | float) -> None:
        self.set_time_axis(start, end, include_seconds=False)

    def values(self, name: str | None = None) -> list[float]:
        """Return a copy of one curve, defaulting to the current editable curve."""

        curve_name = self._editable_curve if name is None else str(name)
        return list(self._series.get(curve_name, []))

    @staticmethod
    def _suggest_value_range(values: Sequence[float]) -> tuple[float, float]:
        if not values:
            return 0.0, 100.0
        raw_minimum = min(values)
        raw_maximum = max(values)
        if math.isclose(raw_minimum, raw_maximum):
            center = (raw_minimum + raw_maximum) / 2.0
            margin = max(abs(center) * 0.25, 1.0)
            return center - margin, center + margin
        span = raw_maximum - raw_minimum
        margin = span * 0.25
        return raw_minimum - margin, raw_maximum + margin

    def _calculate_value_range(self, _values: Sequence[float]) -> tuple[float, float]:
        return self._apply_y_limits(self._edit_value_range)

    def _line_width(self, name: str) -> int:
        return 3 if name == self._editable_curve else 2

    def _axis_x_at(self, position: QPointF) -> float:
        plot_rect = self.plot_rect()
        x = min(max(position.x(), plot_rect.left()), plot_rect.right())
        axis_start, axis_end = self.axis_range()
        return axis_start + (axis_end - axis_start) * (
            x - plot_rect.left()
        ) / plot_rect.width()

    def _nearest_curve_name(self, position: QPointF) -> str | None:
        plot_rect = self.plot_rect()
        if (
            plot_rect.isNull()
            or plot_rect.width() <= 0
            or plot_rect.height() <= 0
            or not plot_rect.contains(position)
        ):
            return None
        axis_x = self._axis_x_at(position)
        minimum, maximum = self._apply_y_limits(self._edit_value_range)
        span = max(1e-12, maximum - minimum)
        nearest: tuple[float, str] | None = None
        for name, values in self._series.items():
            if not values:
                continue
            coordinates = self._x_values_for_series(name, len(values))
            index = self._nearest_point_index(coordinates, axis_x)
            point_y = plot_rect.top() + plot_rect.height() * (
                maximum - values[index]
            ) / span
            candidate = (abs(position.y() - point_y), name)
            if nearest is None or candidate[0] < nearest[0]:
                nearest = candidate
        return nearest[1] if nearest is not None else None

    def _sample_at(self, position: QPointF) -> tuple[int, float] | None:
        values = self._series.get(self._editable_curve, [])
        if not values:
            return None
        plot_rect = self.plot_rect()
        if plot_rect.isNull() or plot_rect.width() <= 0 or plot_rect.height() <= 0:
            return None
        y = min(max(position.y(), plot_rect.top()), plot_rect.bottom())
        axis_x = self._axis_x_at(position)
        coordinates = self._x_values_for_series(self._editable_curve, len(values))
        index = self._nearest_point_index(coordinates, axis_x)
        minimum, maximum = self._apply_y_limits(self._edit_value_range)
        value = maximum - (maximum - minimum) * (
            y - plot_rect.top()
        ) / plot_rect.height()
        if abs(value) < max(1e-9, (maximum - minimum) * 0.001):
            value = 0.0
        return index, float(value)

    def _apply_drag(self, position: QPointF) -> None:
        sample = self._sample_at(position)
        if sample is None:
            return
        index, value = sample
        if self._last_drag_point is None:
            points = [(index, value)]
        else:
            previous_index, previous_value = self._last_drag_point
            distance = abs(index - previous_index)
            if distance == 0:
                points = [(index, value)]
            else:
                direction = 1 if index > previous_index else -1
                points = [
                    (
                        previous_index + direction * offset,
                        previous_value + (value - previous_value) * offset / distance,
                    )
                    for offset in range(1, distance + 1)
                ]
        values = self._series[self._editable_curve]
        for point_index, point_value in points:
            values[point_index] = point_value
            self.pointChanged.emit(point_index, point_value)
            self.curvePointChanged.emit(
                self._editable_curve,
                point_index,
                point_value,
            )
        self._last_drag_point = (index, value)
        self._active_index = index
        self._track_cursor(position)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._series:
            nearest = self._nearest_curve_name(event.position())
            if nearest is not None:
                self.set_editable_curve(nearest)
                self._dragging = True
                self._last_drag_point = None
                self._apply_drag(event.position())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._apply_drag(event.position())
            event.accept()
            return
        nearest = self._nearest_curve_name(event.position())
        if nearest is not None:
            values = self._series[nearest]
            coordinates = self._x_values_for_series(nearest, len(values))
            index = self._nearest_point_index(coordinates, self._axis_x_at(event.position()))
            self._active_index = index
            self.pointHovered.emit(index, values[index])
            self.curvePointHovered.emit(nearest, index, values[index])
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._apply_drag(event.position())
            self._dragging = False
            self._last_drag_point = None
            all_values = [
                value for values in self._series.values() for value in values
            ]
            self._edit_value_range = self._suggest_value_range(all_values)
            self.update()
            self.editFinished.emit()
            self.curveEditFinished.emit(self._editable_curve)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._dragging:
            self._active_index = None
            self.update()
        super().leaveEvent(event)
