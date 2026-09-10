"""Read-only multi-curve plot widget implemented with the Qt paint system."""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

SECONDS_PER_DAY = 24 * 60 * 60


def _format_number(value: int | float) -> str:
    return f"{float(value):.3f}"


def _format_time(seconds: int | float) -> str:
    rounded = min(max(round(float(seconds)), 0), SECONDS_PER_DAY)
    if rounded == SECONDS_PER_DAY:
        return "24:00:00"
    hour, remainder = divmod(rounded, 3600)
    minute, second = divmod(remainder, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}"


class HistoryPlot(QWidget):
    """A lightweight, reusable Qt widget for displaying multiple curves.

    The simplest usage is ``set_curves({name: values})``. Optional X values
    may be shared by every curve or supplied separately for each curve.
    """

    COLORS = (
        QColor("#3e7bfa"),
        QColor("#19a974"),
        QColor("#f59f00"),
        QColor("#d9485f"),
        QColor("#8c62d4"),
        QColor("#14a3a8"),
        QColor("#e76f51"),
        QColor("#457b9d"),
        QColor("#9c6644"),
        QColor("#6a994e"),
        QColor("#c77dff"),
        QColor("#ff70a6"),
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._series: dict[str, list[float]] = {}
        self._series_x_values: dict[str, list[float]] = {}
        self._axis_labels = ("起点", "终点")
        self._x_axis_range: tuple[float, float] | None = None
        self._x_axis_mode = "point_index"
        self._x_axis_decimals = 3
        self._x_axis_suffix = ""
        self._point_axis_prefix = "点 "
        self._x_labels: list[str] = []
        self._y_axis_limits: tuple[float | None, float | None] = (None, None)
        self._last_plot_rect = QRectF()
        self._last_value_range = (0.0, 1.0)
        self._cursor_position: QPointF | None = None
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_curves(
        self,
        curves: Mapping[str, Sequence[float]],
        *,
        x_values: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
    ) -> None:
        """Replace all displayed curves.

        ``x_values`` may be one coordinate sequence shared by all curves, or
        a mapping from curve name to its own coordinates. When omitted, each
        curve is distributed evenly over the current X-axis range.
        """

        normalized: dict[str, list[float]] = {}
        for raw_name, raw_values in curves.items():
            name = str(raw_name)
            values = [float(value) for value in raw_values]
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"曲线 {name} 包含非有限数值")
            normalized[name] = values

        coordinates_by_name: dict[str, list[float]] = {}
        for name, values in normalized.items():
            if x_values is None:
                continue
            source = x_values.get(name) if isinstance(x_values, Mapping) else x_values
            if source is None:
                continue
            coordinates = [float(value) for value in source]
            if len(coordinates) != len(values):
                raise ValueError(
                    f"曲线 {name} 的横坐标数量 {len(coordinates)} "
                    f"与数据点数量 {len(values)} 不一致"
                )
            if not all(math.isfinite(value) for value in coordinates):
                raise ValueError(f"曲线 {name} 的横坐标包含非有限数值")
            if any(left > right for left, right in zip(coordinates, coordinates[1:])):
                raise ValueError(f"曲线 {name} 的横坐标必须按升序排列")
            coordinates_by_name[name] = coordinates

        self._series = normalized
        self._series_x_values = coordinates_by_name
        self._cursor_position = None
        self.update()

    def set_series(
        self,
        series: Mapping[str, Sequence[float]],
        *,
        x_values: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
    ) -> None:
        """Backward-compatible alias for :meth:`set_curves`."""

        self.set_curves(series, x_values=x_values)

    def curves(self) -> dict[str, list[float]]:
        """Return a defensive copy of all displayed curve values."""

        return {name: list(values) for name, values in self._series.items()}

    def set_axis_labels(self, start: str, end: str) -> None:
        self._axis_labels = (str(start), str(end))
        parsed_start = self._parse_time_label(start)
        parsed_end = self._parse_time_label(end)
        if parsed_start is not None and parsed_end is not None:
            start_value = float(parsed_start[0])
            end_value = float(parsed_end[0])
            self._x_axis_range = (
                (start_value, end_value) if start_value <= end_value else None
            )
            self._x_axis_mode = "time"
        else:
            self._x_axis_range = None
            self._x_axis_mode = "custom"
        self.update()

    def set_time_axis(
        self,
        start: int | float,
        end: int | float,
        *,
        include_seconds: bool = True,
    ) -> None:
        start_value = float(start)
        end_value = float(end)
        if not 0 <= start_value <= end_value <= SECONDS_PER_DAY:
            raise ValueError("曲线横轴时段必须位于 00:00:00～24:00:00")
        self._x_axis_range = (start_value, end_value)
        self._x_axis_mode = "time"
        start_text = _format_time(start_value)
        end_text = _format_time(end_value)
        if not include_seconds:
            start_text = start_text[:5]
            end_text = end_text[:5]
        self._axis_labels = (start_text, end_text)
        self.update()

    def set_point_axis(
        self,
        count: int,
        *,
        start: int = 1,
        prefix: str = "点 ",
    ) -> None:
        """Use integer point numbers such as ``点 1`` and ``点 24``."""

        point_count = int(count)
        point_start = int(start)
        if point_count <= 0:
            raise ValueError("点数必须大于 0")
        point_end = point_start + point_count - 1
        self._x_axis_range = (float(point_start), float(point_end))
        self._x_axis_mode = "point"
        self._point_axis_prefix = str(prefix)
        self._axis_labels = (
            f"{self._point_axis_prefix}{point_start}",
            f"{self._point_axis_prefix}{point_end}",
        )
        self.update()

    def set_numeric_axis(
        self,
        start: int | float,
        end: int | float,
        *,
        decimals: int = 3,
        suffix: str = "",
    ) -> None:
        """Use a numeric X axis with configurable precision and unit suffix."""

        start_value = float(start)
        end_value = float(end)
        decimal_count = int(decimals)
        if not math.isfinite(start_value) or not math.isfinite(end_value):
            raise ValueError("数值横轴范围必须是有限数值")
        if start_value >= end_value:
            raise ValueError("数值横轴起点必须小于终点")
        if not 0 <= decimal_count <= 12:
            raise ValueError("数值横轴小数位数必须位于 0～12")
        self._x_axis_range = (start_value, end_value)
        self._x_axis_mode = "numeric"
        self._x_axis_decimals = decimal_count
        self._x_axis_suffix = str(suffix)
        self._axis_labels = (
            self._format_numeric_x(start_value),
            self._format_numeric_x(end_value),
        )
        self.update()

    def _format_numeric_x(self, value: int | float) -> str:
        return f"{float(value):.{self._x_axis_decimals}f}{self._x_axis_suffix}"

    def set_x_labels(self, labels: Sequence[str]) -> None:
        self._x_labels = [str(label) for label in labels]
        if self._x_labels:
            self._x_axis_mode = "labels"
        self.update()

    def axis_range(self) -> tuple[float, float]:
        return self._x_axis_range or (0.0, 1.0)

    def _x_values_for_series(self, name: str, count: int) -> list[float]:
        explicit = self._series_x_values.get(name)
        if explicit is not None and len(explicit) == count:
            return explicit
        start, end = self.axis_range()
        if count <= 1:
            return [start] if count else []
        return [
            start + (end - start) * index / (count - 1) for index in range(count)
        ]

    def series_x_ratios(self, name: str) -> list[float]:
        values = self._series.get(name, [])
        coordinates = self._x_values_for_series(name, len(values))
        start, end = self.axis_range()
        span = max(1e-12, end - start)
        return [(coordinate - start) / span for coordinate in coordinates]

    def plot_rect(self) -> QRectF:
        return QRectF(self._last_plot_rect)

    def value_range(self) -> tuple[float, float]:
        return self._last_value_range

    def set_y_range(
        self,
        minimum: int | float | None = None,
        maximum: int | float | None = None,
    ) -> None:
        """Fix either or both Y limits; omit both arguments to restore auto mode."""

        lower = None if minimum is None else float(minimum)
        upper = None if maximum is None else float(maximum)
        if lower is not None and not math.isfinite(lower):
            raise ValueError("纵轴下限必须是有限数值")
        if upper is not None and not math.isfinite(upper):
            raise ValueError("纵轴上限必须是有限数值")
        if lower is not None and upper is not None and lower >= upper:
            raise ValueError("纵轴下限必须小于上限")
        self._y_axis_limits = (lower, upper)
        self.update()

    def reset_y_range(self) -> None:
        """Restore automatic Y-axis scaling."""

        self.set_y_range()

    def _apply_y_limits(
        self,
        suggested_range: tuple[float, float],
    ) -> tuple[float, float]:
        suggested_minimum, suggested_maximum = suggested_range
        fixed_minimum, fixed_maximum = self._y_axis_limits
        minimum = suggested_minimum if fixed_minimum is None else fixed_minimum
        maximum = suggested_maximum if fixed_maximum is None else fixed_maximum
        if minimum >= maximum:
            margin = max(abs(minimum), abs(maximum), 1.0) * 0.1
            if fixed_minimum is not None and fixed_maximum is None:
                maximum = minimum + margin
            elif fixed_minimum is None and fixed_maximum is not None:
                minimum = maximum - margin
        return minimum, maximum

    def _calculate_value_range(self, values: Sequence[float]) -> tuple[float, float]:
        minimum = min(0.0, min(values))
        maximum = max(values)
        if math.isclose(minimum, maximum):
            maximum = minimum + 1.0
        return self._apply_y_limits((minimum, maximum))

    def _line_width(self, _name: str) -> int:
        return 2

    @staticmethod
    def _parse_time_label(label: str) -> tuple[int, bool] | None:
        parts = str(label).split(":")
        if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
            return None
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        if minute > 59 or second > 59:
            return None
        if hour == 24 and minute == 0 and second == 0:
            return SECONDS_PER_DAY, len(parts) == 3
        if hour > 23:
            return None
        return hour * 3600 + minute * 60 + second, len(parts) == 3

    def _cursor_x_label(self, x_value: float, ratio: float, count: int) -> str:
        if self._x_axis_mode == "time":
            start = self._parse_time_label(self._axis_labels[0])
            end = self._parse_time_label(self._axis_labels[1])
            start_has_seconds = start[1] if start is not None else True
            end_has_seconds = end[1] if end is not None else True
            if start_has_seconds or end_has_seconds:
                return _format_time(x_value)
            minute = round(x_value / 60)
            return _format_time(min(minute * 60, SECONDS_PER_DAY))[:5]
        if self._x_axis_mode == "numeric":
            return self._format_numeric_x(x_value)
        if self._x_axis_mode == "point":
            return f"{self._point_axis_prefix}{round(x_value)}"
        if len(self._x_labels) == count and self._x_labels:
            index = round(ratio * max(1, count - 1))
            return self._x_labels[min(max(index, 0), count - 1)]
        index = round(ratio * max(1, count - 1))
        return f"点 {index + 1}/{count}"

    @staticmethod
    def _nearest_point_index(coordinates: Sequence[float], target: float) -> int:
        if not coordinates:
            return 0
        position = bisect_left(coordinates, target)
        if position <= 0:
            return 0
        if position >= len(coordinates):
            return len(coordinates) - 1
        before = position - 1
        return (
            before
            if abs(coordinates[before] - target) <= abs(coordinates[position] - target)
            else position
        )

    def _cursor_payload(self) -> dict | None:
        position = self._cursor_position
        plot_rect = self.plot_rect()
        nonempty = [(name, values) for name, values in self._series.items() if values]
        if (
            position is None
            or plot_rect.isNull()
            or not plot_rect.contains(position)
            or not nonempty
        ):
            return None
        ratio = min(
            1.0,
            max(0.0, (position.x() - plot_rect.left()) / plot_rect.width()),
        )
        reference_count = max(len(values) for _name, values in nonempty)
        axis_start, axis_end = self.axis_range()
        axis_x_value = axis_start + (axis_end - axis_start) * ratio
        minimum, maximum = self.value_range()
        y_ratio = min(
            1.0,
            max(0.0, (position.y() - plot_rect.top()) / plot_rect.height()),
        )
        y_value = maximum - (maximum - minimum) * y_ratio
        samples = []
        values_text: dict[str, str] = {}
        for series_index, (name, values) in enumerate(nonempty):
            coordinates = self._x_values_for_series(name, len(values))
            point_index = self._nearest_point_index(coordinates, axis_x_value)
            value = float(values[point_index])
            samples.append(
                (series_index, name, values, coordinates, point_index, value)
            )
            values_text[name] = _format_number(value)
        return {
            "ratio": ratio,
            "x_label": self._cursor_x_label(
                axis_x_value,
                ratio,
                reference_count,
            ),
            "y_value": _format_number(y_value),
            "values": values_text,
            "samples": samples,
        }

    def cursor_snapshot(self) -> dict | None:
        """Return the visible cursor readout for tests and status integrations."""

        payload = self._cursor_payload()
        if payload is None:
            return None
        return {
            "x_label": payload["x_label"],
            "y_value": payload["y_value"],
            "values": dict(payload["values"]),
        }

    def _track_cursor(self, position: QPointF) -> None:
        plot_rect = self.plot_rect()
        next_position = (
            QPointF(position)
            if not plot_rect.isNull() and plot_rect.contains(position)
            else None
        )
        if next_position != self._cursor_position:
            self._cursor_position = next_position
            self.update()

    def mouseMoveEvent(self, event) -> None:
        self._track_cursor(event.position())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._cursor_position = None
        self.update()
        super().leaveEvent(event)

    def _draw_cursor(self, painter: QPainter) -> None:
        payload = self._cursor_payload()
        if payload is None:
            return
        plot_rect = self.plot_rect()
        minimum, maximum = self.value_range()
        x = plot_rect.left() + plot_rect.width() * float(payload["ratio"])
        position = self._cursor_position
        if position is None:
            return
        y = min(max(position.y(), plot_rect.top()), plot_rect.bottom())

        painter.save()
        painter.setClipRect(plot_rect)
        painter.setPen(QPen(QColor("#5d6b7d"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, plot_rect.top()), QPointF(x, plot_rect.bottom()))
        painter.drawLine(QPointF(plot_rect.left(), y), QPointF(plot_rect.right(), y))
        axis_start, axis_end = self.axis_range()
        axis_span = max(1e-12, axis_end - axis_start)
        for (
            series_index,
            _name,
            _values,
            coordinates,
            point_index,
            value,
        ) in payload["samples"]:
            point_ratio = (coordinates[point_index] - axis_start) / axis_span
            point_x = plot_rect.left() + plot_rect.width() * point_ratio
            point_y = plot_rect.top() + plot_rect.height() * (
                maximum - value
            ) / (maximum - minimum)
            color = self.COLORS[series_index % len(self.COLORS)]
            painter.setBrush(color)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(QPointF(point_x, point_y), 5, 5)
        painter.restore()

        available_rows = max(1, min(12, int((plot_rect.height() - 42) // 18)))
        visible_samples = payload["samples"][:available_rows]
        hidden_count = len(payload["samples"]) - len(visible_samples)
        header_lines = [
            f"X: {payload['x_label']}",
            f"Y: {payload['y_value']}",
        ]
        sample_lines = [
            f"{name}: {_format_number(value)}"
            for (
                _series_index,
                name,
                _values,
                _coordinates,
                _point_index,
                value,
            ) in visible_samples
        ]
        if hidden_count:
            sample_lines.append(f"另有 {hidden_count} 条曲线")
        lines = header_lines + sample_lines
        font_metrics = painter.fontMetrics()
        panel_width = min(
            max(170, max(font_metrics.horizontalAdvance(line) for line in lines) + 34),
            max(170, int(plot_rect.width()) - 8),
        )
        panel_height = 14 + len(lines) * 18
        panel_x = x + 12
        panel_y = y + 12
        if panel_x + panel_width > plot_rect.right():
            panel_x = x - panel_width - 12
        if panel_y + panel_height > plot_rect.bottom():
            panel_y = y - panel_height - 12
        panel_x = min(
            max(panel_x, plot_rect.left() + 4),
            plot_rect.right() - panel_width - 4,
        )
        panel_y = min(
            max(panel_y, plot_rect.top() + 4),
            plot_rect.bottom() - panel_height - 4,
        )
        panel_rect = QRectF(panel_x, panel_y, panel_width, panel_height)
        painter.setBrush(QColor(255, 255, 255, 238))
        painter.setPen(QPen(QColor("#aab6c5"), 1))
        painter.drawRoundedRect(panel_rect, 5, 5)
        text_y = panel_y + 18
        painter.setPen(QColor("#26364a"))
        for line in header_lines:
            painter.drawText(QPointF(panel_x + 10, text_y), line)
            text_y += 18
        for (
            series_index,
            name,
            _values,
            _coordinates,
            _point_index,
            value,
        ) in visible_samples:
            color = self.COLORS[series_index % len(self.COLORS)]
            painter.fillRect(QRectF(panel_x + 10, text_y - 9, 10, 4), color)
            painter.setPen(QColor("#26364a"))
            display = font_metrics.elidedText(
                f"{name}: {_format_number(value)}",
                Qt.TextElideMode.ElideRight,
                panel_width - 34,
            )
            painter.drawText(QPointF(panel_x + 25, text_y), display)
            text_y += 18
        if hidden_count:
            painter.drawText(QPointF(panel_x + 10, text_y), f"另有 {hidden_count} 条曲线")

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        left, right, bottom = 64, 24, 44
        legend_positions: dict[int, tuple[int, int, int, str]] = {}
        legend_x = left
        legend_y = 8
        for index, (name, series) in enumerate(self._series.items()):
            if not series:
                continue
            display_name = painter.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideRight, 220
            )
            item_width = max(
                92, 24 + painter.fontMetrics().horizontalAdvance(display_name)
            )
            if legend_x + item_width > self.width() - right and legend_x > left:
                legend_x = left
                legend_y += 20
            legend_positions[index] = (
                legend_x,
                legend_y,
                item_width,
                display_name,
            )
            legend_x += item_width
        top = max(28, legend_y + 14 if legend_positions else 28)
        plot_width = max(1, self.width() - left - right)
        plot_height = max(1, self.height() - top - bottom)
        self._last_plot_rect = QRectF(left, top, plot_width, plot_height)
        axis_pen = QPen(QColor("#b5bfcc"), 1)
        painter.setPen(axis_pen)
        painter.drawLine(left, top, left, top + plot_height)
        painter.drawLine(left, top + plot_height, left + plot_width, top + plot_height)

        painter.setPen(QColor("#718096"))
        painter.drawText(left, top + plot_height + 25, self._axis_labels[0])
        painter.drawText(
            left + plot_width - 70,
            top + plot_height + 25,
            70,
            20,
            Qt.AlignmentFlag.AlignRight,
            self._axis_labels[1],
        )

        values = [value for series in self._series.values() for value in series]
        if not values:
            self._last_value_range = (0.0, 1.0)
            painter.setPen(QColor("#718096"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无曲线数据")
            return
        minimum, maximum = self._calculate_value_range(values)
        self._last_value_range = (minimum, maximum)

        painter.setPen(QColor("#718096"))
        for grid in range(5):
            y = top + plot_height * grid / 4
            value = maximum - (maximum - minimum) * grid / 4
            painter.setPen(QPen(QColor("#edf1f5"), 1))
            painter.drawLine(left, int(y), left + plot_width, int(y))
            painter.setPen(QColor("#718096"))
            painter.drawText(
                4,
                int(y) - 8,
                54,
                18,
                Qt.AlignmentFlag.AlignRight,
                _format_number(value),
            )

        for index, (name, series) in enumerate(self._series.items()):
            if not series:
                continue
            color = self.COLORS[index % len(self.COLORS)]
            painter.setPen(QPen(color, self._line_width(name)))
            path = QPainterPath()
            coordinates = self._x_values_for_series(name, len(series))
            axis_start, axis_end = self.axis_range()
            axis_span = max(1e-12, axis_end - axis_start)
            for point_index, (x_value, value) in enumerate(zip(coordinates, series)):
                x = left + plot_width * (x_value - axis_start) / axis_span
                y = top + plot_height * (maximum - value) / (maximum - minimum)
                if point_index == 0:
                    path.moveTo(QPointF(x, y))
                else:
                    path.lineTo(QPointF(x, y))
            painter.save()
            painter.setClipRect(self._last_plot_rect)
            painter.drawPath(path)
            painter.restore()
            legend_x, legend_y, legend_width, display_name = legend_positions[index]
            painter.fillRect(legend_x, legend_y, 14, 4, color)
            painter.setPen(QColor("#344054"))
            painter.drawText(
                legend_x + 19,
                legend_y - 7,
                legend_width - 19,
                20,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                display_name,
            )

        self._draw_cursor(painter)
