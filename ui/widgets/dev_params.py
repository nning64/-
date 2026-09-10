# -*- coding: utf-8 -*-
"""设备参数面板: 展示并支持修改 device_params 表(风机 WT / 柴发 DG 运行参数)。

- value 列可编辑; 点"保存"统一写库。
- main.py 每帧读 device_params 并热更新到模型对象, 保存后下一仿真秒即生效(不用重启)。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QTableWidget, QTableWidgetItem, QPushButton,
                               QHeaderView, QAbstractItemView)

from .. import db, theme

_COLS = ["设备", "参数名", "值", "单位", "说明"]


class DevParams(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        self._reload()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        lay.addWidget(self._table)

        row = QHBoxLayout()
        self._btn_reload = QPushButton("刷新")
        self._btn_save = QPushButton("保存修改")
        save_tip = QLabel("改完点保存, main 下一帧即生效")
        save_tip.setObjectName("sub")
        row.addWidget(self._btn_reload)
        row.addWidget(self._btn_save)
        row.addStretch(1)
        row.addWidget(save_tip)
        lay.addLayout(row)

        self._btn_reload.clicked.connect(self._reload)
        self._btn_save.clicked.connect(self._save)

    def _reload(self):
        rows = db.device_params_all()
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = self._pretty_name(r["device"], r["key"])
            vals = [r["device"], name, r["value"] or "", r["unit"] or "",
                    r["remark"] or ""]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if j == 2:              # 值列可编辑
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(i, j, item)
            # 隐藏存 device/key, 保存时用
            self._table.item(i, 0).setData(Qt.UserRole, (r["device"], r["key"]))

    @staticmethod
    def _pretty_name(device, key):
        face = {"cut_in_wind": "切入风速", "rated_wind": "额定风速",
                "cut_out_wind": "切出风速", "rated_power": "额定功率",
                "dg_rated": "额定功率", "dg_p_max": "出力上限",
                "dg_p_min": "出力下限"}
        return face.get(key, key)

    def _save(self):
        for i in range(self._table.rowCount()):
            item0 = self._table.item(i, 0)
            if item0 is None:
                continue
            dev, key = item0.data(Qt.UserRole)
            val = self._table.item(i, 2).text()
            db.update_device_param(dev, key, val)
