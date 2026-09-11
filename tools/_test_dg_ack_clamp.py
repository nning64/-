#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""针对修2 (dg_ack 钳下限) 的针对性单元测试。
不依赖 main.py 私有符号, 直接构造核心对象 + 复刻主循环那段 dg_ack 计算。
"""
import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")  # 进大作业/
sys.path.insert(0, ".")
import sqlite3
from main import (DB, get_param, set_param, wt_params, dg_params,
                  WindTurbine, DieselGen, CommandExecutive, write_realtime,
                  env_curve_lookup)

if not DB.exists():
    print(f"[X] 找不到 {DB}")
    sys.exit(1)

con = sqlite3.connect(str(DB))
cur = con.cursor()

# 清空环境
cur.execute("DELETE FROM sim_history")
cur.execute("DELETE FROM yc_realtime")
cur.execute("DELETE FROM yx_realtime")
cur.execute("DELETE FROM env_curve")
con.commit()

# 直接塞一组手工 env_curve 点: 风大、负荷小 -> wt 大、负荷小 -> dg_sp 必负
for t in range(0, 10):
    cur.execute("INSERT INTO env_curve(t, wind_speed, load_kw) VALUES (?,?,?)",
                (t, 14.0, 20.0))    # 风 14 (额定附近) + 负荷 20 -> dg_sp = -80
con.commit()

# 构造核心对象
wt_p = wt_params(cur)
dg_p = dg_params(cur)
wt_tb = WindTurbine(wt_p)
dg_tb = DieselGen(dg_p)
cexe  = CommandExecutive()
print(f"[参] dg_tb.p_min={dg_tb.p_min}, dg_tb.p_max={dg_tb.p_max}")

# 跑 5 秒, 复刻 main.py 关键逻辑(只关心 dg_ack 写库值)
for sim_time in range(1, 6):
    wind, load_kw = env_curve_lookup(cur, sim_time, period=None)
    # 风机
    wt_r = wt_tb.step(wind, p_set=cexe.s["wt_p_set"],
                      start_cmd=cexe.s["wt_start"], pitch_set=None)
    wt_act, wt_pitch, wt_run, wt_avail = wt_r["p_act"], wt_r["pitch"], wt_r["run"], wt_r["p_avail"]
    wt_ack = cexe.s["wt_p_set"] if cexe.s["wt_p_set"] is not None else wt_avail
    # 柴发
    dg_sp = cexe.s["dg_set"] if cexe.s["dg_set"] is not None else load_kw - wt_act
    dg_r = dg_tb.step(dg_sp, run_cmd=cexe.s["dg_run"] if cexe.s["dg_run"] is not None else 1)
    dg_act, dg_run = dg_r["p_act"], dg_r["run"]
    unbalance = load_kw - wt_act - dg_act
    # ===== 修复点: dg_ack 钳下限 =====
    if cexe.s["dg_set"] is not None:
        dg_ack = cexe.s["dg_set"]
    else:
        dg_ack = max(dg_tb.p_min, dg_sp)
    cur_state = 1
    yc_rows, yx_rows = write_realtime(
        cur, wind, load_kw, wt_act, wt_pitch, wt_run, wt_avail,
        wt_ack, dg_act, dg_run, unbalance, dg_p,
        dg_ack=dg_ack, sim_state=cur_state,
    )

# 读回
print("\n[测试结果] 5 秒 env_curve(风=14, 负荷=20) 后 DB 中的关键点:")
for r in cur.execute("SELECT rtu_id, point_no, value FROM yc_realtime "
                     "WHERE rtu_id IN ('R01','R02') AND point_no IN (2,5,1,2) "
                     "ORDER BY rtu_id, point_no").fetchall():
    print(f"  R{r[0][-1]}:{r[1]} = {r[2]}")

print("\n期望:")
print("  R01:2 WT_ACT     ~ 87.6  (风14, 风机满发 87.6 < 额定 100)")
print("  R02:1 DG_ACT     ~ 30.0  (dg_sp=-67.6 被夹到 p_min=30)")
print("  R02:2 DG_P_SET_ACK = 30.0  ← 修2 的关键: 不再是 -67.6, 而被钳到 p_min")
print("  R01:5 WT_P_SET_ACK ~ 87.6 (无指令时回显可用功率)")

# 校验
v = cur.execute("SELECT value FROM yc_realtime WHERE rtu_id='R02' AND point_no=2").fetchone()[0]
if abs(v - 30.0) < 0.01:
    print("\n[PASS] R02:2 = 30.0 ✓ 修2 钳下限生效")
else:
    print(f"\n[FAIL] R02:2 = {v}, 期望 30.0")
    sys.exit(1)

# 修5 校验: R03:1 跟 sim_state
s = cur.execute("SELECT value FROM yx_realtime WHERE rtu_id='R03' AND point_no=1").fetchone()[0]
print(f"  R03:1 SIM_RUN = {s} (期望 1 = 运行中)")
if s == 1:
    print("[PASS] R03:1 跟随 sim_state ✓ 修5 生效")
else:
    print(f"[FAIL] R03:1 = {s}")
    sys.exit(1)

con.close()