"""验证 R02:2 柴发功率回显 —— 2026-09-10 改为'永远有值':
EMS 下过 YT -> 回显指令值;
EMS 没下过 -> 回显系统目标 dg_sp (EMS 没指令时 A 就地补缺的目标).
不再 DELETE 行, 避免 main.py 重启/B 未连时 UI 闪烁 "--".
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import write_realtime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'grid.db')
con = sqlite3.connect(DB); cur = con.cursor()

# 场景 1: EMS 没下发指令 -> R02:2 应等于系统目标 dg_sp (= load_kw - wt_act)
write_realtime(
    cur,
    wind=8.0, load_kw=120.0,
    wt_act=50.0, wt_pitch=0.0, wt_run=1, wt_avail=80.0,
    wt_ack=80.0,
    dg_act=70.0, dg_run=1, unbalance=0.0,
    dg_p={'dg_rated': 300.0},
    dg_ack=70.0,        # 系统目标 = load(120) - wt(50) = 70
)
con.commit()
v = cur.execute(
    "SELECT value FROM yc_realtime WHERE rtu_id='R02' AND point_no=2"
).fetchone()[0]
print(f'[1] EMS 未下指令, dg_ack 用系统目标 dg_sp=70 -> R02:2 = {v}  (期望 70.0)')
assert v == 70.0, v

# 场景 2: EMS 下发 80 -> R02:2 应等于 80 (覆盖上面的 70)
write_realtime(
    cur,
    wind=8.0, load_kw=120.0,
    wt_act=50.0, wt_pitch=0.0, wt_run=1, wt_avail=80.0,
    wt_ack=80.0,
    dg_act=80.0, dg_run=1, unbalance=-10.0,
    dg_p={'dg_rated': 300.0},
    dg_ack=80.0,        # EMS 真发了 80
)
con.commit()
v = cur.execute(
    "SELECT value FROM yc_realtime WHERE rtu_id='R02' AND point_no=2"
).fetchone()[0]
print(f'[2] EMS 下发 80   -> R02:2 = {v}  (期望 80.0)')
assert v == 80.0, v

# 场景 3: EMS 撤回(回到 None) -> R02:2 应再次等于 dg_sp(此时变了因为 wt_act 变了)
write_realtime(
    cur,
    wind=8.0, load_kw=120.0,
    wt_act=60.0, wt_pitch=0.0, wt_run=1, wt_avail=80.0,
    wt_ack=80.0,
    dg_act=60.0, dg_run=1, unbalance=0.0,
    dg_p={'dg_rated': 300.0},
    dg_ack=60.0,        # dg_sp = 120-60 = 60 (EMS 没下,用系统目标)
)
con.commit()
v = cur.execute(
    "SELECT value FROM yc_realtime WHERE rtu_id='R02' AND point_no=2"
).fetchone()[0]
print(f'[3] EMS 撤回, dg_ack 用 dg_sp=60  -> R02:2 = {v}  (期望 60.0)')
assert v == 60.0, v

print('\n全部通过: R02:2 永远有值; EMS 指令优先, 无指令用系统目标 dg_sp 兜底.')
con.close()