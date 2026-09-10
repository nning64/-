"""
dump_history.py - 抽查 sim_history 表的落数据情况

用法:
    python tools/dump_history.py            # 显示头/尾各 10 条
    python tools/dump_history.py --limit 30 # 显示头/尾各 30 条
    python tools/dump_history.py --all      # 显示全部
"""

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB   = ROOT / 'grid.db'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--all',   action='store_true')
    args = ap.parse_args()

    if not DB.exists():
        print(f'[X] 找不到 {DB}, 先 python tools/build_db.py')
        sys.exit(1)

    c   = sqlite3.connect(str(DB))
    cur = c.cursor()

    n = cur.execute('SELECT count(*) FROM sim_history').fetchone()[0]
    t_now = cur.execute(
        "SELECT value FROM sim_params WHERE key='sim_time'"
    ).fetchone()
    t_now = t_now[0] if t_now else '?'

    print(f'sim_history: {n} 条   sim_time = {t_now}')
    print()

    limit = 10**9 if args.all else args.limit

    print('=== 头 {} 条 ==='.format(min(limit, n)))
    sql = (
        'SELECT sim_time, wind_speed, load_kw, wt_act_kw, dg_act_kw, '
        '       wt_pitch_deg, wt_run, dg_run, unbalance_kw '
        'FROM sim_history ORDER BY sim_time LIMIT ?'
    )
    for r in cur.execute(sql, (limit,)).fetchall():
        print(
            f'  t={r[0]:4d}  wind={r[1]:5.1f}m/s  load={r[2]:6.1f}kW  '
            f'wt={r[3]:6.1f}kW(p={r[5]:4.1f}° r={r[6]})  '
            f'dg={r[4]:6.1f}kW  unbal={r[8]:+6.1f}kW'
        )

    if n > limit * 2:
        print()
        print('=== 尾 {} 条 ==='.format(limit))
        for r in cur.execute(
            sql.replace('ORDER BY sim_time LIMIT ?',
                        'ORDER BY sim_time DESC LIMIT ?'),
            (limit,),
        ).fetchall()[::-1]:
            print(
                f'  t={r[0]:4d}  wind={r[1]:5.1f}m/s  load={r[2]:6.1f}kW  '
                f'wt={r[3]:6.1f}kW(p={r[5]:4.1f}° r={r[6]})  '
                f'dg={r[4]:6.1f}kW  unbal={r[8]:+6.1f}kW'
            )

    c.close()

if __name__ == '__main__':
    main()
