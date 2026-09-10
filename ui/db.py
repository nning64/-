# -*- coding: utf-8 -*-
"""grid.db 只读访问层 —— UI 唯一的取数通道。

界面不直接拼 SQL, 所有查询走这里的函数, 列名变化只改这一处。
说明:
- UI 是 A 端"驾驶舱", 直接读同一个 grid.db(WAL 模式支持多进程并发读写,
  正在跑的 main.py 写、本 UI 读互不打架)。
- 这里默认只读; 本地指令注入 / 拖曲线写库会单独开可写方法并明确标注。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 大作业/
DB_PATH = ROOT / "grid.db"


@contextmanager
def _conn(ro: bool = True):
    """短连接上下文: 用完自动 close。UI 每秒轮询, 不能攒连接。

    注意: 可写连接(ro=False)必须在 close 前 commit —— sqlite3 默认
    开启隐式事务, 只 execute 不 commit 的话 close 时会把改动全部回滚,
    表现为"写库毫无反应"(2026-09-08 踩过的坑)。
    """
    if ro:
        c = sqlite3.connect("file:%s?mode=ro" % DB_PATH.as_posix(), uri=True)
    else:
        c = sqlite3.connect(str(DB_PATH))
    try:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout = 2000")        # 等 main 释放写锁, 最多 2s
        yield c
        if not ro:
            c.commit()                                 # 不提交 = 白写
    finally:
        c.close()


# ---------------- 元信息: 四遥点表(中文名/单位/量程) ----------------

def yc_meta() -> list[dict]:
    """遥测点表: [(rtu, pt, code, name, unit, min, max), ...] 按 RTU/点序。"""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT rtu_id, point_no, code, name, unit, min_val, max_val"
            " FROM rtu_yc_info ORDER BY rtu_id, point_no")]


def yx_meta() -> list[dict]:
    """遥信点表。"""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT rtu_id, point_no, code, name, value_desc"
            " FROM rtu_yx_info ORDER BY rtu_id, point_no")]


def cmd_meta() -> dict:
    """遥控/遥调点表(本地注入按钮用): key=(rtu,type,pt) -> 说明 dict。"""
    out = {}
    with _conn() as c:
        for r in c.execute("SELECT rtu_id, point_no, code, name"
                           " FROM rtu_yk_info"):
            out[(r["rtu_id"], "YK", r["point_no"])] = {
                "code": r["code"], "name": r["name"], "kind": "YK"}
        for r in c.execute("SELECT rtu_id, point_no, code, name, range_desc"
                           " FROM rtu_yt_info"):
            out[(r["rtu_id"], "YT", r["point_no"])] = {
                "code": r["code"], "name": r["name"],
                "range": r["range_desc"], "kind": "YT"}
    return out


# ---------------- 实时值 ----------------

def sim_info() -> dict:
    """sim_params 里 UI 关心的几项。"""
    keys = ("sim_time", "sim_state", "sim_start_real", "control_cycle_s")
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM sim_params").fetchall()
    d = {r["key"]: r["value"] for r in rows}
    return {k: d.get(k, "") for k in keys}


def yc_values() -> dict:
    """yc_realtime 现值: (rtu, pt) -> value(float)。"""
    with _conn() as c:
        rows = c.execute("SELECT rtu_id, point_no, value"
                         " FROM yc_realtime").fetchall()
    return {(r["rtu_id"], r["point_no"]): r["value"] for r in rows}


def yx_values() -> dict:
    """yx_realtime 现值: (rtu, pt) -> value(int)。"""
    with _conn() as c:
        rows = c.execute("SELECT rtu_id, point_no, value"
                         " FROM yx_realtime").fetchall()
    return {(r["rtu_id"], r["point_no"]): r["value"] for r in rows}


# ---------------- 曲线数据 ----------------

def recent_sim(n: int = 150) -> list[dict]:
    """sim_history 最近 n 秒(按 sim_time 升序返回, 好直接画)。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT sim_time, wind_speed, load_kw, wt_act_kw, dg_act_kw,"
            "       wt_pitch_deg, unbalance_kw"
            " FROM sim_history ORDER BY sim_time DESC LIMIT ?", (n,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_history() -> dict:
    """清空历史表(sim_history/yc_history/yx_history)—— 给"启动/复位"用。

    设计: UI 自己立刻清(不等 main), 这样曲线/SCADA 表/历史曲线面板会
    立刻变"等待…"空状态, 视觉反馈即时; main 接 start 命令后下一帧
    开始写新数据, 曲线自然跟着动。
    返回各表实际删除行数, 便于在 UI 提示里告诉用户。
    """
    with _conn(ro=False) as c:
        n_sim = c.execute("DELETE FROM sim_history").rowcount
        n_yc  = c.execute("DELETE FROM yc_history").rowcount
        n_yx  = c.execute("DELETE FROM yx_history").rowcount
        c.commit()
    return {"sim_history": n_sim, "yc_history": n_yc, "yx_history": n_yx}


def scenario_period() -> int:
    """env_curve 的总仿真秒数 (= max(t) + 1, 即一周期长度)。0 = 未加载场景。"""
    with _conn() as c:
        m = c.execute("SELECT MAX(t) FROM env_curve").fetchone()[0]
    return int(m + 1) if m is not None else 0


def env_curve() -> list[dict]:
    """env_curve 全部控制点(拖拽编辑用): t / wind_speed / load_kw。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT t, wind_speed, load_kw FROM env_curve ORDER BY t"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 可写操作(注入/拖拽, UI 主动触发时才用) ----------------

def write_env_point(t: int, field: str, value: float) -> None:
    """改写 env_curve 的一个点(field 只允许 wind_speed / load_kw)。

    main.py 每秒查表, 拖完的下一仿真秒就会按新值跑(不用重启)。
    """
    assert field in ("wind_speed", "load_kw")
    with _conn(ro=False) as c:
        c.execute("UPDATE env_curve SET %s = ? WHERE t = ?"
                  % field, (float(value), int(t)))


def write_command(rtu: str, cmd_type: str, pt: int, val, src: str = "UI_DEMO") -> str:
    """本地指令注入: 把一条 YK/YT 写进信箱(state=0), 主循环下一秒执行。

    等价于 EMS 下令, 但不占 TCP 角色(不会把真 EMS 顶下线)。
    返回 'OK' 或错误说明。
    """
    assert cmd_type in ("YK", "YT")
    table = "yk_realtime" if cmd_type == "YK" else "yt_realtime"
    col = "cmd" if cmd_type == "YK" else "value"
    with _conn(ro=False) as c:
        c.execute(
            "INSERT OR REPLACE INTO %s (rtu_id, point_no, %s, src, state)"
            " VALUES (?, ?, ?, ?, 0)" % (table, col),
            (rtu, int(pt), float(val) if cmd_type == "YT" else int(val), src))
    return "OK"


# ---------------- 仿真控制 (P3) ----------------

def sim_params_all() -> dict:
    """sim_params 全部键值(仿真控制台读起始/结束/步长/状态)。"""
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM sim_params").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_sim_param(key: str, value) -> None:
    """写 sim_params 的一项(可写连接)。key 不存在则建行。"""
    with _conn(ro=False) as c:
        c.execute("INSERT OR IGNORE INTO sim_params(key, value) VALUES (?,?)",
                  (key, str(value)))
        c.execute(
            "UPDATE sim_params SET value=?, "
            "updated_at=datetime('now','localtime') WHERE key=?",
            (str(value), key))


def send_ctrl(cmd: str) -> None:
    """下发一条仿真控制命令: start/pause/resume/stop。main 主循环下帧消费。"""
    set_sim_param("ctrl_cmd", cmd)


# ---------------- 设备参数 (P3) ----------------

def device_params_all() -> list[dict]:
    """device_params 全部行(风机/柴发运行参数)。"""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT device, key, value, unit, remark FROM device_params "
            "ORDER BY device, key")]


def update_device_param(device: str, key: str, value) -> None:
    """改一个设备参数值(Ui 保存时用; main 每帧读表热生效)。"""
    with _conn(ro=False) as c:
        c.execute("UPDATE device_params SET value=? WHERE device=? AND key=?",
                  (str(value), device, key))


# ---------------- SCADA 实时表 (P3) ----------------

def scada_rows() -> list[dict]:
    """四遥实时值 + 点表元信息合成一张全量 SCADA 表展示行(含 YC/YX/YK/YT)。"""
    out = []
    with _conn() as c:
        for r in c.execute(
            "SELECT y.rtu_id, y.point_no, i.code, i.name, i.unit, y.value, "
            "       y.quality, y.updated_at "
            "FROM yc_realtime y JOIN rtu_yc_info i "
            "     ON i.rtu_id=y.rtu_id AND i.point_no=y.point_no "
            "ORDER BY y.rtu_id, y.point_no"):
            out.append({"rtu": r["rtu_id"], "type": "YC", "pt": r["point_no"],
                        "code": r["code"], "name": r["name"],
                        "val": r["value"], "unit": r["unit"] or "",
                        "quality": r["quality"], "updated": r["updated_at"]})
        for r in c.execute(
            "SELECT y.rtu_id, y.point_no, i.code, i.name, y.value, y.updated_at "
            "FROM yx_realtime y JOIN rtu_yx_info i "
            "     ON i.rtu_id=y.rtu_id AND i.point_no=y.point_no "
            "ORDER BY y.rtu_id, y.point_no"):
            out.append({"rtu": r["rtu_id"], "type": "YX", "pt": r["point_no"],
                        "code": r["code"], "name": r["name"],
                        "val": r["value"], "unit": "",
                        "quality": None, "updated": r["updated_at"]})
        for r in c.execute(
            "SELECT y.rtu_id, y.point_no, i.code, i.name, y.cmd, y.state, "
            "       y.src, y.created_at "
            "FROM yk_realtime y JOIN rtu_yk_info i "
            "     ON i.rtu_id=y.rtu_id AND i.point_no=y.point_no "
            "ORDER BY y.rtu_id, y.point_no"):
            out.append({"rtu": r["rtu_id"], "type": "YK", "pt": r["point_no"],
                        "code": r["code"], "name": r["name"],
                        "val": r["cmd"], "unit": "", "quality": None,
                        "updated": r["created_at"],
                        "state": r["state"], "src": r["src"]})
        for r in c.execute(
            "SELECT y.rtu_id, y.point_no, i.code, i.name, y.value, y.state, "
            "       y.src, y.created_at "
            "FROM yt_realtime y JOIN rtu_yt_info i "
            "     ON i.rtu_id=y.rtu_id AND i.point_no=y.point_no "
            "ORDER BY y.rtu_id, y.point_no"):
            out.append({"rtu": r["rtu_id"], "type": "YT", "pt": r["point_no"],
                        "code": r["code"], "name": r["name"],
                        "val": r["value"], "unit": "", "quality": None,
                        "updated": r["created_at"],
                        "state": r["state"], "src": r["src"]})
    return out


# ---------------- SCADA 历史曲线 (P3) ----------------

def history_points() -> list[dict]:
    """历史曲线可选点: yc_history/yx_history 里出现过的点(get 中文名)。"""
    out = []
    with _conn() as c:
        for r in c.execute(
            "SELECT DISTINCT h.rtu_id, h.point_no, i.name "
            "FROM yc_history h JOIN rtu_yc_info i "
            "     ON i.rtu_id=h.rtu_id AND i.point_no=h.point_no "
            "ORDER BY h.rtu_id, h.point_no"):
            out.append({"rtu": r["rtu_id"], "type": "YC", "pt": r["point_no"],
                        "name": r["name"]})
        for r in c.execute(
            "SELECT DISTINCT h.rtu_id, h.point_no, i.name "
            "FROM yx_history h JOIN rtu_yx_info i "
            "     ON i.rtu_id=h.rtu_id AND i.point_no=h.point_no "
            "ORDER BY h.rtu_id, h.point_no"):
            out.append({"rtu": r["rtu_id"], "type": "YX", "pt": r["point_no"],
                        "name": r["name"]})
    return out


def history_series(rtu: str, typ: str, pt: int, t0: int, t1: int) -> list:
    """某历史点在 [t0,t1] 区间内的采样序列: [(sim_time, value), ...]。"""
    tbl = "yc_history" if typ == "YC" else "yx_history"
    with _conn() as c:
        return [tuple(r) for r in c.execute(
            "SELECT sim_time, value FROM %s WHERE rtu_id=? AND point_no=? "
            "AND sim_time BETWEEN ? AND ? ORDER BY sim_time"
            % tbl, (rtu, pt, t0, t1))]


def history_max_t() -> int:
    """历史数据已记到的最新时刻(最近N秒范围用的终点)。没有任何数据返回 0。"""
    with _conn() as c:
        v = c.execute(
            "SELECT MAX(m) FROM ("
            "  SELECT MAX(sim_time) AS m FROM sim_history "
            "  UNION SELECT MAX(sim_time) FROM yc_history "
            "  UNION SELECT MAX(sim_time) FROM yx_history)"
        ).fetchone()[0]
    return int(v or 0)


# ---------------- 运行日志 (P3) ----------------

def recent_log(n: int = 100) -> list[dict]:
    """log 表最近 n 条(按时间倒序)。"""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT level, src, msg, created_at FROM log "
            "ORDER BY id DESC LIMIT ?", (n,))]
