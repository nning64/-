"""基于 QWidget/QPainter 的鼠标手绘时序曲线控件。"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from curve_data import CurveConfig, CurveData


class CurveEditor(QWidget):
    """可直接加入任意 Qt 布局的单曲线编辑控件。"""

    pointChanged = pyqtSignal(int, float)  # 某个点改变
    curveChanged = pyqtSignal(object)  # 整条曲线副本改变
    editingFinished = pyqtSignal()  # 一次鼠标笔画完成

    def __init__(self, curve: CurveData | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.curve = curve or CurveData.constant(
            CurveConfig("input", "输入曲线", "", 0.0, 100.0), 50.0
        )
        self._dragging = False
        self._last_sample: tuple[int, float] | None = None
        self._hover_sample: tuple[int, float] | None = None
        self._playhead_seconds: float | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(360, 220)

    def sizeHint(self) -> QSize:
        return QSize(720, 300)

    def set_curve(self, curve: CurveData, emit_signal: bool = False) -> None:
        self.curve = curve
        self._hover_sample = None
        self.update()
        if emit_signal:
            self.curveChanged.emit(self.values())

    def set_values(self, values: Sequence[float], emit_signal: bool = True) -> None:
        self.curve.replace(values)
        self.update()
        if emit_signal:
            self.curveChanged.emit(self.values())

    def values(self) -> list[float]:
        return self.curve.values()

    def value_at(self, seconds: float) -> float:
        return self.curve.value_at(seconds)

    def set_playhead(self, seconds: float | None) -> None:
        self._playhead_seconds = seconds
        self.update()

    def plot_rect(self) -> QRectF:
        return QRectF(
            70.0,
            40.0,
            max(1.0, self.width() - 92.0),
            max(1.0, self.height() - 84.0),
        )

    def position_to_sample(self, position: QPointF) -> tuple[int, float] | None:
        """把鼠标坐标映射为（采样点序号，工程量）。"""

        rect = self.plot_rect()
        if not rect.contains(position):
            return None
        config = self.curve.config
        x_ratio = (position.x() - rect.left()) / rect.width()
        y_ratio = (position.y() - rect.top()) / rect.height()
        index = round(x_ratio * (config.sample_count - 1))
        value = config.maximum - y_ratio * (config.maximum - config.minimum)
        return index, min(max(value, config.minimum), config.maximum)

    def _apply_position(self, position: QPointF) -> None:
        sample = self.position_to_sample(position)
        if sample is None:
            return
        index, value = sample
        if self._last_sample is None:
            changed = [(index, self.curve.set_point(index, value))]
        else:
            old_index, old_value = self._last_sample
            changed = self.curve.set_segment(old_index, old_value, index, value)
        self._last_sample = (index, value)
        self._hover_sample = (index, self.curve.values()[index])
        for point_index, point_value in changed:
            self.pointChanged.emit(point_index, point_value)
        self.curveChanged.emit(self.values())
        self.update()

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.position_to_sample(event.position()) is not None
        ):
            self._dragging = True
            self._last_sample = None
            self._apply_position(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._apply_position(event.position())
            event.accept()
            return
        sample = self.position_to_sample(event.position())
        if sample is None:
            self._hover_sample = None
        else:
            index, _value = sample
            self._hover_sample = (index, self.curve.values()[index])
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._apply_position(event.position())
            self._dragging = False
            self._last_sample = None
            self.editingFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._dragging:
            self._hover_sample = None
            self.update()
        super().leaveEvent(event)

    def _point(self, index: int, value: float) -> QPointF:
        rect = self.plot_rect()
        config = self.curve.config
        x = rect.left() + rect.width() * index / (config.sample_count - 1)
        y = rect.bottom() - rect.height() * (
            value - config.minimum
        ) / (config.maximum - config.minimum)
        return QPointF(x, y)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        config = self.curve.config
        rect = self.plot_rect()

        painter.setPen(QColor("#26364a"))
        painter.drawText(12, 23, f"{config.name}  [{config.unit}]")
        painter.setPen(QPen(QColor("#aab5c3"), 1))
        painter.drawRoundedRect(rect, 4, 4)

        # 五条横纵网格及坐标标签。
        for step in range(5):
            ratio = step / 4
            y = rect.top() + rect.height() * ratio
            x = rect.left() + rect.width() * ratio
            painter.setPen(QPen(QColor("#e8edf3"), 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            value = config.maximum - ratio * (config.maximum - config.minimum)
            painter.setPen(QColor("#526275"))
            painter.drawText(
                QRectF(4, y - 10, 60, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.1f}",
            )
            hour = 24 * step // 4
            painter.drawText(
                QRectF(x - 28, rect.bottom() + 7, 56, 20),
                Qt.AlignmentFlag.AlignCenter,
                f"{hour:02d}:00",
            )

        values = self.curve.values()
        path = QPainterPath(self._point(0, values[0]))
        for index, value in enumerate(values[1:], start=1):
            path.lineTo(self._point(index, value))
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QPen(QColor(config.color), 2.2))
        painter.drawPath(path)
        painter.restore()

        # 橙色线为宿主程序设定的当前仿真时刻。
        if self._playhead_seconds is not None:
            index = round(
                min(
                    max(self._playhead_seconds / config.sample_interval_seconds, 0),
                    config.sample_count - 1,
                )
            )
            point = self._point(index, self.value_at(self._playhead_seconds))
            painter.setPen(QPen(QColor("#d97706"), 1.2, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(point.x(), rect.top()), QPointF(point.x(), rect.bottom()))

        if self._hover_sample is not None:
            index, value = self._hover_sample
            point = self._point(index, value)
            painter.setBrush(QColor(config.color))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(point, 5, 5)
            seconds = index * config.sample_interval_seconds
            text = (
                f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}  "
                f"{value:.3f} {config.unit}"
            )
            box = QRectF(
                min(point.x() + 10, self.width() - 175),
                max(point.y() - 34, 6),
                165,
                26,
            )
            painter.setBrush(QColor(255, 255, 255, 240))
            painter.setPen(QPen(QColor("#aab5c3"), 1))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor("#26364a"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

