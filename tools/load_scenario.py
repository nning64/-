"""
load_scenario.py - 把 scenarios/ 下的验收场景 CSV 灌进 grid.db 的 env_curve 表

用法(在 PyCharm 右键 Run, 或命令行):
    python tools/load_scenario.py
    python tools/load_scenario.py scenarios/02_适宜风速_风机满发.csv

设计要点:
- CSV 格式: t,wind_speed,load_kw   (无表头,t 为整数秒)
- 每次加载都会整表清空 env_curve 再写入(仓库一次只装一场戏)
- 加载完自动写 sim_params.scenario_file 记录路径
- 加载完自动清空 sim_history / yc_history / yx_history / 指令信箱与指令历史
  (避免新旧场景混在一起; 每场戏从"没人指挥"开始)
"""

import csv
import sqlite3
import sys
from pathlib import Path

HERE  = Path(__file__).resolve().parent
ROOT  = HERE.parent
DB    = ROOT / 'grid.db'
SCEN  = ROOT / 'scenarios'

def list_scenarios():
    if not SCEN.exists():
        return []
    return sorted(p for p in SCEN.glob('*.csv'))

def pick_default():
    files = list_scenarios()
    if not files:
        return None
    # 优先选"适宜风速"场景作为默认,风机会跑起来
    for f in files:
        if '适宜' in f.name:
            return f
    return files[0]

def load(csv_path: Path, db_path: Path = DB):
    if not db_path.exists():
        raise FileNotFoundError(
            f'找不到 {db_path}, 先 python tools/build_db.py 建库'
        )
    if not csv_path.exists():
        raise FileNotFoundError(f'找不到场景文件 {csv_path}')

    conn = sqlite3.connect(str(db_path))
    cur  = conn.cursor()

    # 读 CSV
    rows = []
    with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []

        # 列名兼容: 优先用 wind_speed; 如果是 wind_speed_m_s 也认
        col_t   = 't'   if 't'   in fields else None
        col_ws  = None
        for cand in ('wind_speed', 'wind_speed_m_s'):
            if cand in fields:
                col_ws = cand
                break
        col_ld  = 'load_kw' if 'load_kw' in fields else None

        if not (col_t and col_ws and col_ld):
            raise ValueError(
                f'CSV 缺少必要列。需要 t/wind_speed(load_kw), 实际 {fields}'
            )

        for r in reader:
            t   = int(float(r[col_t]))
            ws  = float(r[col_ws])
            ld  = float(r[col_ld])
            rows.append((t, ws, ld))

    if not rows:
        raise ValueError(f'{csv_path} 是空文件')

    t_min = min(r[0] for r in rows)
    t_max = max(r[0] for r in rows)
    print(f'  CSV 范围: t = {t_min} ~ {t_max}  ({len(rows)} 行)')
    print(f'  风速: {min(r[1] for r in rows):.1f} ~ {max(r[1] for r in rows):.1f} m/s')
    print(f'  负荷: {min(r[2] for r in rows):.1f} ~ {max(r[2] for r in rows):.1f} kW')

    # 替换旧数据: 一次只装一场戏, 整表清空再写入
    # (不能只删 t 范围: 若上一场戏比这场长, 长出来的尾巴会残留成脏数据)
    cur.execute('DELETE FROM env_curve')
    cur.executemany(
        'INSERT INTO env_curve(t, wind_speed, load_kw) VALUES (?,?,?)',
        rows,
    )

    # 清旧 sim_history, 避免前后场景叠加
    cur.execute('DELETE FROM sim_history')

    # 清空变动历史账本 (P1-4 起 yc/yx_history 会逐条累积; 换场景必须清,
    # 否则两场戏的变位记录混在一起; 清空后下一次运行每个点先落一条基线)
    cur.execute('DELETE FROM yc_history')
    cur.execute('DELETE FROM yx_history')

    # 清空指令信箱与指令历史 (P2-3 起 EMS/风机指令会排队并留档;
    # 换场景从"没人指挥"开始, 新剧情由 EMS 重新下发指令)
    cur.execute('DELETE FROM yk_realtime')
    cur.execute('DELETE FROM yt_realtime')
    cur.execute('DELETE FROM yk_history')
    cur.execute('DELETE FROM yt_history')

    # 记录当前场景文件 + 把仿真时间归零
    cur.execute(
        "UPDATE sim_params SET value=? WHERE key='scenario_file'",
        (str(csv_path.relative_to(ROOT)),),
    )
    cur.execute("UPDATE sim_params SET value='0' WHERE key='sim_time'")

    conn.commit()
    conn.close()
    print(f'  [OK] 已写入 env_curve + 清空 sim_history/变位账本/指令信箱 '
          f'+ sim_time 归零')

def main():
    args = sys.argv[1:]

    if args:
        csv_path = Path(args[0])
        if not csv_path.is_absolute():
            csv_path = (ROOT / csv_path).resolve()
    else:
        csv_path = pick_default()

    if csv_path is None:
        print('[X] scenarios/ 下没有 CSV, 先 python tools/gen_scenario.py 生成场景')
        sys.exit(1)

    print(f'>> 加载场景: {csv_path.name}')
    load(csv_path)

    print()
    print('验证:')
    conn = sqlite3.connect(str(DB))
    n = conn.execute('SELECT count(*) FROM env_curve').fetchone()[0]
    head = conn.execute(
        'SELECT t, wind_speed, load_kw FROM env_curve ORDER BY t LIMIT 3'
    ).fetchall()
    tail = conn.execute(
        'SELECT t, wind_speed, load_kw FROM env_curve ORDER BY t DESC LIMIT 3'
    ).fetchall()
    scen = conn.execute(
        "SELECT value FROM sim_params WHERE key='scenario_file'"
    ).fetchone()[0]
    conn.close()

    print(f'  env_curve 共 {n} 条')
    print(f'  头 3 条: {head}')
    print(f'  尾 3 条: {tail}')
    print(f'  当前场景文件 = {scen}')

if __name__ == '__main__':
    main()
