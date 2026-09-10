# -*- coding: utf-8 -*-
"""SCADA 实时表: 全量四遥(遥测/遥信/遥控/遥调)当前值 + 元信息 一览。

数据来自 db.scada_rows()(yc/yx/yk/yt_realtime JOIN 点表), 每秒刷新。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QTableWidget,
                               QTableWidgetItem, QHeaderView)

from .. import db, theme

_COLS = ["设备", "类别", "点号", "名称", "值", "单位", "质量/状态", "更新时间"]
_STATE_TEXT = {0: "待执行", 1: "已执行", 2: "失败"}


class ScadaTable(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._reload)
        self._timer.start(1000)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self._table)

    def _reload(self):
        rows = db.scada_rows()
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            val = r.get("val")
            if r["type"] == "YC":
                val_s = "%.2f" % val if val is not None else "—"
            else:
                val_s = str(val)
            qual = ""
            if r["type"] == "YC":
                qual = "GOOD" if r.get("quality", 0) == 0 else "BAD"
            elif r["type"] in ("YK", "YT"):
                st = r.get("state")
                qual = _STATE_TEXT.get(st, str(st)) if st is not None else ""
            cell = [r["rtu"], r["type"], str(r["pt"]), r["name"], val_s,
                    r.get("unit", ""), qual, r.get("updated", "")]
            for j, v in enumerate(cell):
                item = QTableWidgetItem(str(v))
                if j == 4:
                    item.setForeground(QColor(theme.TEXT))
                self._table.setItem(i, j, item)
