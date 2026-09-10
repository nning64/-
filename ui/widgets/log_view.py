# -*- coding: utf-8 -*-
"""运行日志面板: 展示 log 表内容, 可按级别过滤、自动刷新。

来源: main.py(MAIN) 与 comm/server.py(SERVER) 写进 log 表的关键事件。
不记 DEBUG 心跳那种高频噪音。
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QTableWidget, QTableWidgetItem, QComboBox,
                               QCheckBox, QPushButton, QHeaderView)

from .. import db, theme

_COLS = ["级别", "来源", "内容", "时间"]
_LEVELCOLOR = {"INFO": theme.GREEN, "WARN": theme.AMBER, "ERROR": theme.RED}


class LogView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._reload)
        self._timer.start(1000)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(QLabel("级别"))
        self._cb_level = QComboBox()
        self._cb_level.addItems(["全部", "INFO", "WARN", "ERROR"])
        self._cb_level.currentIndexChanged.connect(self._reload)
        self._cb_auto = QCheckBox("自动刷新")
        self._cb_auto.setChecked(True)
        self._cb_auto.toggled.connect(self._toggle_auto)
        btn = QPushButton("刷新")
        btn.clicked.connect(self._reload)
        top.addWidget(self._cb_level)
        top.addWidget(self._cb_auto)
        top.addWidget(btn)
        top.addStretch(1)
        lay.addLayout(top)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self._table)

    def _toggle_auto(self, on):
        if on:
            self._timer.start(1000)
        else:
            self._timer.stop()

    def _reload(self):
        rows = db.recent_log(200)
        level = self._cb_level.currentText()
        if level != "全部":
            rows = [r for r in rows if r["level"] == level]
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, k in enumerate(["level", "src", "msg", "created_at"]):
                item = QTableWidgetItem(str(r.get(k, "")))
                if k == "level":
                    item.setForeground(
                        QColor(_LEVELCOLOR.get(r.get(k, ""), theme.TEXT)))
                self._table.setItem(i, j, item)
