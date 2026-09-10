"""曲线数据、插值和 JSON/CSV 导入导出；本文件不依赖 Qt。"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


@dataclass(frozen=True)
class CurveConfig:
    """定义一条曲线的名称、工程量范围和采样规格。"""

    key: str
    name: str
    unit: str
    minimum: float
    maximum: float
    color: str = "#2f80ed"
    sample_count: int = 1440
    sample_interval_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.name.strip():
            raise ValueError("key 和 name 不能为空")
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            raise ValueError("曲线上下限必须是有限数值")
        if self.maximum <= self.minimum:
            raise ValueError("maximum 必须大于 minimum")
        if self.sample_count < 2 or self.sample_interval_seconds <= 0:
            raise ValueError("采样数必须至少为 2，采样间隔必须大于 0")

    @property
    def duration_seconds(self) -> int:
        return self.sample_count * self.sample_interval_seconds


class CurveData:
    """保存曲线点，并提供限幅、拖画补点和时间插值。"""

    def __init__(self, config: CurveConfig, values: Sequence[float]):
        self.config = config
        self._values: list[float] = []
        self.replace(values)

    @classmethod
    def constant(cls, config: CurveConfig, value: float) -> "CurveData":
        return cls(config, [value] * config.sample_count)

    @classmethod
    def from_function(
        cls,
        config: CurveConfig,
        function: Callable[[int, float], float],
    ) -> "CurveData":
        return cls(
            config,
            [
                function(index, index * config.sample_interval_seconds)
                for index in range(config.sample_count)
            ],
        )

    def values(self) -> list[float]:
        """返回数据副本，避免外部绕过上下限检查。"""

        return list(self._values)

    def _clamp(self, value: float) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("曲线点必须是有限数值")
        return min(max(number, self.config.minimum), self.config.maximum)

    def replace(self, values: Sequence[float]) -> None:
        if len(values) != self.config.sample_count:
            raise ValueError(
                f"需要 {self.config.sample_count} 个采样点，实际收到 {len(values)} 个"
            )
        self._values = [self._clamp(value) for value in values]

    def set_point(self, index: int, value: float) -> float:
        if not 0 <= index < len(self._values):
            raise IndexError(index)
        self._values[index] = self._clamp(value)
        return self._values[index]

    def set_segment(
        self,
        start_index: int,
        start_value: float,
        end_index: int,
        end_value: float,
    ) -> list[tuple[int, float]]:
        """线性补齐两次鼠标事件之间跳过的采样点。"""

        if not 0 <= start_index < len(self._values):
            raise IndexError(start_index)
        if not 0 <= end_index < len(self._values):
            raise IndexError(end_index)
        start_value = self._clamp(start_value)
        end_value = self._clamp(end_value)
        distance = abs(end_index - start_index)
        if distance == 0:
            return [(end_index, self.set_point(end_index, end_value))]
        direction = 1 if end_index > start_index else -1
        changed = []
        for offset in range(distance + 1):
            ratio = offset / distance
            index = start_index + direction * offset
            value = start_value + (end_value - start_value) * ratio
            changed.append((index, self.set_point(index, value)))
        return changed

    def value_at(self, seconds: float) -> float:
        """返回任意时刻的线性插值，超出时间轴时使用端点值。"""

        position = float(seconds) / self.config.sample_interval_seconds
        position = min(max(position, 0.0), len(self._values) - 1)
        left = int(math.floor(position))
        right = min(left + 1, len(self._values) - 1)
        ratio = position - left
        return self._values[left] + (self._values[right] - self._values[left]) * ratio

    def to_dict(self) -> dict:
        return {"config": asdict(self.config), "values": self.values()}

    @classmethod
    def from_dict(cls, data: dict) -> "CurveData":
        return cls(CurveConfig(**data["config"]), data["values"])


def save_json(path: str | Path, curves: Iterable[CurveData]) -> None:
    items = list(curves)
    if not items:
        raise ValueError("至少需要一条曲线")
    data = {"schema_version": 1, "curves": [curve.to_dict() for curve in items]}
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_json(path: str | Path) -> list[CurveData]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 1:
        raise ValueError("不支持的曲线文件版本")
    curves = [CurveData.from_dict(item) for item in data.get("curves", [])]
    if not curves:
        raise ValueError("文件中没有曲线")
    return curves


def save_csv(path: str | Path, curves: Iterable[CurveData]) -> None:
    """以 UTF-8 BOM 编码导出，方便 Windows Excel 直接打开。"""

    items = list(curves)
    if not items:
        raise ValueError("至少需要一条曲线")
    first = items[0].config
    if any(
        curve.config.sample_count != first.sample_count
        or curve.config.sample_interval_seconds != first.sample_interval_seconds
        for curve in items[1:]
    ):
        raise ValueError("CSV 导出要求各曲线采样规格相同")
    columns = [curve.values() for curve in items]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["sample_index", "time_seconds"]
            + [f"{item.config.name} ({item.config.unit})" for item in items]
        )
        for index in range(first.sample_count):
            writer.writerow(
                [index, index * first.sample_interval_seconds]
                + [column[index] for column in columns]
            )
