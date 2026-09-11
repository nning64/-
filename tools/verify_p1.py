"""
verify_p1.py - P1 冒烟自检: 6 个场景逐个"灌入 + 快跑 + 自动查账"

大白话: 这一条命令帮你把 P1 的所有成果整体体检一遍。
  P1 练好了风机/柴发身体、接好了公告栏和历史账本, 但代码一改就可能
  悄悄弄坏某一环 —— 自检就是"一键体检": 跑不跑得动? 数对不对?

体检项目(每场戏都查):
  ① 跑不崩: 模拟器一口气跑完不报错
  ② 公告栏遥测(yc_realtime) 11 个点全有数值、没有坏值
  ③ 数值不越界: 每个点都在点表写的量程内 (风速0~40 / 风机0~100 / 柴发0~300 ...)
     ※ 唯一例外"不平衡量 UNBAL": 开环(还没接 EMS 调度)时它超出 ±50
       是预期物理事实(没人管, 账不平), 只提醒、不算错
  ④ 全景日记(sim_history) 每秒一行、一个洞都没有
  ⑤ 公告栏遥信(yx_realtime) 8 个状态点齐全

用法(在项目根目录):
    python tools/verify_p1.py
成功标志: 最后一行  6/6 全部 PASS
"""

import contextlib
import io
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from load_scenario import load   # noqa: E402
import main as sim               # noqa: E402   (同一套主程序, 不另写一遍)

SCEN_DIR = ROOT / 'scenarios'
SPEED    = 300                   # 300 倍速快跑, 60 秒剧情约 0.3 秒跑完


def check_one(csv_path):
    """跑完一场戏, 返回 (结果列表[(名称, ok, 详情)], 警告列表)。"""
    results = []
    warns   = []
    name    = csv_path.name

    # --- 第 0 步: 灌入场景, 并问出剧情长度 ---
    load(csv_path)
    conn = sqlite3.connect(str(ROOT / 'grid.db'))
    cur  = conn.cursor()
    n_rows = cur.execute('SELECT count(*) FROM env_curve').fetchone()[0]
    t_max  = cur.execute('SELECT max(t) FROM env_curve').fetchone()[0]
    conn.close()

    # --- 第 1 步: 一口气快跑完整场 ---
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            sim.run_loop(None, speed=SPEED, until=t_max)
        results.append(('① 跑完整场不崩', True, f'{t_max} 秒剧情跑完'))
    except SystemExit as e:
        results.append(('① 跑完整场不崩', False, f'程序自行退出 code={e}, 见下'))
        results.append(('②③④⑤ 因跑崩跳过', False, '先修上面的问题再体检'))
        return results, warns, buf.getvalue()
    except Exception as e:
        results.append(('① 跑完整场不崩', False, f'抛异常: {type(e).__name__}: {e}'))
        results.append(('②③④⑤ 因跑崩跳过', False, '先修上面的问题再体检'))
        return results, warns, buf.getvalue()

    # --- 第 2 步: 查账 ---
    conn = sqlite3.connect(str(ROOT / 'grid.db'))
    cur  = conn.cursor()

    # ② 遥测公告栏: 11 行, 全有值, 无坏值
    yc = cur.execute(
        'SELECT rtu_id, point_no, value, quality FROM yc_realtime'
    ).fetchall()
    if len(yc) != 11:
        results.append(('② 公告栏遥测 11 点齐全', False,
                        f'实际 {len(yc)} 行'))
    else:
        bad = [r for r in yc if r[2] is None or r[3] != 0]
        if bad:
            results.append(('② 公告栏遥测 11 点齐全', False,f'{len(bad)} 个点坏值/空值: {bad[:3]}'))
        else:
            results.append(('② 公告栏遥测 11 点齐全', True, '全有值, 质量全好'))

    # ③ 量程: 逐点对照点表, UNBAL(R03-2) 例外只提醒
    ov = []
    for rtu, pt, val, _q in yc:
        row = cur.execute(
            'SELECT code, min_val, max_val FROM rtu_yc_info '
            'WHERE rtu_id=? AND point_no=?', (rtu, pt)
        ).fetchone()
        if row is None:
            continue
        code, mn, mx = row
        if mn is None or mx is None:
            continue
        if not (mn <= val <= mx):
            if rtu == 'R03' and pt == 2:
                warns.append(f'{code}={val} 超量程[{mn},{mx}] '
                             '(开环失衡属预期, 待 EMS 调度接手)')
            else:
                ov.append(f'{rtu}-{pt} {code}={val} 越界[{mn},{mx}]')
    if ov:
        results.append(('③ 数值都在量程内', False, '; '.join(ov[:4])))
    else:
        results.append(('③ 数值都在量程内', True,
                        '10 个物理点全合规' + ('(UNBAL 有提醒)' if warns else '')))

    # ④ 全景日记: 每秒一行, 无空洞
    cnt, mn_t, mx_t = cur.execute(
        'SELECT count(*), min(sim_time), max(sim_time) FROM sim_history'
    ).fetchone()
    if cnt == mx_t - mn_t + 1 and cnt > 0:
        results.append(('④ 日记每秒一行无空洞', True,
                        f'{cnt} 行, t={mn_t}~{mx_t} 连续'))
    else:
        results.append(('④ 日记每秒一行无空洞', False,
                        f'{cnt} 行但 t={mn_t}~{mx_t}, 有 {mx_t - mn_t + 1 - cnt} 个空洞'))

    # ⑤ 遥信公告栏: 8 行
    n_yx = cur.execute('SELECT count(*) FROM yx_realtime').fetchone()[0]
    if n_yx == 8:
        results.append(('⑤ 公告栏遥信 8 点齐全', True, ''))
    else:
        results.append(('⑤ 公告栏遥信 8 点齐全', False, f'实际 {n_yx} 行'))

    conn.close()
    return results, warns, buf.getvalue()


def main():
    files = sorted(SCEN_DIR.glob('*.csv'))
    if not files:
        print('[X] scenarios/ 下没有 CSV')
        sys.exit(1)

    print('=' * 64)
    print('P1 冒烟自检: 6 场景逐个体检 (300 倍速快跑完整场)')
    print('=' * 64)

    passed = 0
    for csv_path in files:
        results, warns, detail = check_one(csv_path)
        ok = all(r[1] for r in results)
        passed += 1 if ok else 0
        mark = '[PASS]' if ok else '[FAIL]'
        print()
        print(f'{mark} {csv_path.name}')
        for rname, rok, rinfo in results:
            print(f'     {"✓" if rok else "✗"} {rname:<18} {rinfo}')
        for w in warns:
            print(f'     ! {w}')
        if not ok:
            print('     ---- 最后 12 行运行输出(排查用) ----')
            tail = [ln for ln in detail.splitlines() if ln.strip()][-12:]
            for ln in tail:
                print('     | ' + ln)

    print()
    print('=' * 64)
    if passed == len(files):
        print(f'结果: {passed}/{len(files)} 全部 PASS')
    else:
        print(f'结果: {passed}/{len(files)} PASS, 有 FAIL 请修')
    print('=' * 64)
    sys.exit(0 if passed == len(files) else 1)


if __name__ == '__main__':
    main()
