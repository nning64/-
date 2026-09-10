# -*- coding: utf-8 -*-
"""轻量系统日志: 把关键事件写进 grid.db 的 log 表, 供 UI"运行日志"面板展示。

main.py(仿真) 与 comm/server.py(总机) 共用这一个入口。
只记 INFO/WARN/ERROR 这类"事件", 不记 DEBUG 心跳那种高频噪音。
连库失败不抛异常 —— 日志丢一条无所谓, 绝不能拖垮仿真/总机。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "grid.db"


def log(level: str, src: str, msg, db=None) -> None:
    """向 log 表追加一条。level: INFO/WARN/ERROR; src: 模块名(MAIN/SERVER/...)。"""
    try:
        con = sqlite3.connect(str(db or DB), timeout=5)
        con.execute("PRAGMA busy_timeout = 5000")
        con.execute("INSERT INTO log(level, src, msg) VALUES (?,?,?)",
                    (level, src, str(msg)))
        con.commit()
        con.close()
    except Exception:
        pass
