# -*- coding: utf-8 -*-
"""实时四遥表: 按 RTU 分三张设备卡, 每秒刷新 YC 数值 + YX 状态灯。

- 中文名/单位/量程都从 rtu_yc_info / rtu_yx_info 读, 不在代码里写死。
- YX 灯语义按 code 分类:
    通信/仿真类(COMM_EMS COMM_WT WIFI_STA SIM_RUN): 1=绿 0=红
    故障/告警类(WT_FAULT DG_ALARM):                   0=绿(正常) 1=红
    启停类(WT_RUN DG_RUN):                            1=绿 0=灰(正常停机)
"""
from __future__ import annotations

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QGroupBox, QGridLayout, QScrollArea, QFrame)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

from .. import db
from .. import theme

_RTU_TITLE = {"R01": "R01 风机", "R02": "R02 柴发", "R03": "R03 电网负荷"}


def _desc_map(value_desc: str) -> dict:
    """'0=停机,1=运行' -> {0:'停机', 1:'运行'}。"""
    m = {}
    for part in str(value_desc).split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                m[int(k)] = v.strip()
            except ValueError:
                pass
    return m


def _yx_light_color(code: str, val: int) -> str:
    """遥信灯该亮的颜色。"""
    if code in ("COMM_EMS", "COMM_WT", "WIFI_STA", "SIM_RUN"):
        if val == 2:                      # SIM_RUN=2 暂停
            return theme.AMBER
        return theme.GREEN if val == 1 else theme.RED
    if code in ("WT_FAULT", "DG_ALARM"):
        return theme.GREEN if val == 0 else theme.RED
    return theme.GREEN if val == 1 else theme.GRAY      # WT_RUN / DG_RUN


class RtuView(QWidget):
    """三张设备卡纵向排, 外裹滚动区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._yc_labels = {}    # (rtu, pt) -> QLabel(值)
        self._yx_state = {}     # (rtu, pt) -> (灯 QLabel, 文字 QLabel)
        self._unit_w = 0

        self._build()

    # ---------------- 构建 ----------------
    def _build(self):
        yc_meta = db.yc_meta()
        yx_meta = db.yx_meta()

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(8)

        for rtu in ("R01", "R02", "R03"):
            gb = self._build_card(rtu,
                                  [m for m in yc_meta if m["rtu_id"] == rtu],
                                  [m for m in yx_meta if m["rtu_id"] == rtu])
            v.addWidget(gb)
        v.addStretch(1)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(body)
        sc.setFrameShape(QFrame.NoFrame)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sc)

    def _build_card(self, rtu, yc_rows, yx_rows):
        gb = QGroupBox(_RTU_TITLE.get(rtu, rtu))
        box = QVBoxLayout(gb)
        box.setSpacing(6)

        if yc_rows:
            cap = QLabel("遥测 YC")
            cap.setObjectName("sub")
            box.addWidget(cap)
            for m in yc_rows:
                row = QHBoxLayout()
                name = QLabel("%s  %s" % (m["name"], m["code"]))
                name.setObjectName("sub")
                name.setFixedWidth(170)
                val = QLabel("--")
                val.setFont(QFont("Consolas", 15, QFont.Bold))
                val.setStyleSheet("color: %s;" % theme.TEXT)
                val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                unit = QLabel(m["unit"] or "-")
                unit.setObjectName("sub")
                unit.setFixedWidth(36)
                row.addWidget(name)
                row.addStretch(1)
                row.addWidget(val)
                row.addWidget(unit)
                box.addLayout(row)
                self._yc_labels[(rtu, m["point_no"])] = val

        if yx_rows:
            sep = QFrame(); sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet("color: %s;" % theme.BORDER)
            box.addWidget(sep)
            cap = QLabel("遥信 YX")
            cap.setObjectName("sub")
            box.addWidget(cap)
            for m in yx_rows:
                row = QHBoxLayout()
                lamp = QLabel(" ")
                lamp.setFixedSize(14, 14)
                lamp.setStyleSheet(theme.lamp_style(theme.GRAY))
                state = QLabel("")
                state.setObjectName("sub")
                row.addWidget(lamp)
                row.addSpacing(4)
                row.addWidget(QLabel(m["name"]))
                row.addStretch(1)
                row.addWidget(state)
                box.addLayout(row)
                self._yx_state[(rtu, m["point_no"])] = (lamp, state,
                                                        m["code"],
                                                        _desc_map(m["value_desc"]))

        box.addStretch(1)
        return gb

    # ---------------- 刷新 ----------------
    def refresh(self, yc_vals: dict, yx_vals: dict):
        for (rtu, pt), lab in self._yc_labels.items():
            v = yc_vals.get((rtu, pt))
            if v is None:
                lab.setText("--")
            else:
                lab.setText("%.1f" % v)
        for (rtu, pt), (lamp, state, code, dmap) in self._yx_state.items():
            v = yx_vals.get((rtu, pt))
            if v is None:
                lamp.setStyleSheet(theme.lamp_style(theme.GRAY))
                state.setText("?")
            else:
                lamp.setStyleSheet(theme.lamp_style(
                    _yx_light_color(code, int(v))))
                state.setText(dmap.get(int(v), str(v)))
