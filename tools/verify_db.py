"""
verify_db.py - 一键验证 grid.db 结构和点表内容

用法 (任一处都可):
    python tools/verify_db.py

不依赖任何参数 / 引号转义,自动定位项目根目录的 grid.db
"""
import sqlite3
import os
import sys
from pathlib import Path

# 自动定位项目根目录(本脚本在 tools/ 下,父目录即项目根)
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB   = ROOT / 'grid.db'

def main():
    if not DB.exists():
        print(f'[X] 未找到 {DB}')
        print('    请先在项目根目录执行:')
        print('    python tools/build_db.py')
        sys.exit(1)

    c = sqlite3.connect(str(DB))
    cur = c.cursor()

    def table_count(name):
        return cur.execute(f'SELECT count(*) FROM {name}').fetchone()[0]

    print('=' * 60)
    print(f'GRID.DB 验证报告 ({DB})')
    print('=' * 60)
    print(f'文件大小: {DB.stat().st_size:,} bytes')
    sql_tables = "SELECT name FROM sqlite_master WHERE type='table'"
    print(f"表总数:   {len(c.execute(sql_tables).fetchall())}")
    print()

    print('=== YC 遥测 (', table_count('rtu_yc_info'), '点) ===')
    for rtu, no, code, name, unit, mnv, mxv, dead, src in cur.execute(
        'SELECT rtu_id,point_no,code,name,unit,min_val,max_val,deadband,source FROM rtu_yc_info ORDER BY rtu_id,point_no'
    ).fetchall():
        u  = unit or '-'
        mn = f'{mnv}'  if mnv is not None else '_'
        mx = f'{mxv}'  if mxv is not None else '_'
        print(f'  {rtu} #{no:>2} {code:<14} {name:<14} {u:>5} range[{mn},{mx}] dead={dead} <- {src}')
    print()

    print('=== YX 遥信 (', table_count('rtu_yx_info'), '点) ===')
    for rtu, no, code, name, desc in cur.execute(
        'SELECT rtu_id,point_no,code,name,value_desc FROM rtu_yx_info ORDER BY rtu_id,point_no'
    ).fetchall():
        print(f'  {rtu} #{no:>2} {code:<14} {name:<14} ({desc})')
    print()

    print('=== YK 遥控 (', table_count('rtu_yk_info'), '点) ===')
    for rtu, no, code, name, desc in cur.execute(
        'SELECT rtu_id,point_no,code,name,value_desc FROM rtu_yk_info ORDER BY rtu_id,point_no'
    ).fetchall():
        print(f'  {rtu} #{no:>2} {code:<14} {name:<14} ({desc})')
    print()

    print('=== YT 遥调 (', table_count('rtu_yt_info'), '点) ===')
    for rtu, no, code, name, unit, desc, src in cur.execute(
        'SELECT rtu_id,point_no,code,name,unit,range_desc,source FROM rtu_yt_info ORDER BY rtu_id,point_no'
    ).fetchall():
        u = unit or '-'
        d = desc or '-'
        print(f'  {rtu} #{no:>2} {code:<14} {name:<14} {u:>5} range:{d} <- {src}')
    print()

    print('=== RTU 注册 (', table_count('rtu'), '个) ===')
    for rtu, name, dev in cur.execute(
        'SELECT rtu_id,name,device_type FROM rtu ORDER BY rtu_id'
    ).fetchall():
        print(f'  {rtu}  {name:<10} type={dev}')
    print()

    print('=== 设备参数 (', table_count('device_params'), '项) ===')
    n_null = 0
    for dev, key, val, unit in cur.execute(
        'SELECT device,key,value,unit FROM device_params ORDER BY device,key'
    ).fetchall():
        v  = val if val is not None else '待定'
        u  = unit or '-'
        if val is None: n_null += 1
        print(f'  {dev} {key:<15} = {v:<10} {u}')
    print()

    print('=== sim_params (', table_count('sim_params'), '项) ===')
    for key, val, upd in cur.execute('SELECT key,value,updated_at FROM sim_params').fetchall():
        print(f'  {key:<20} = {val}')
    print()

    # 验收清单
    print('=' * 60)
    print('验收 P0 骨架')
    print('=' * 60)
    checks = [
        ('rtu_yc_info >= 11 点',           c.execute('SELECT count(*) FROM rtu_yc_info').fetchone()[0] >= 11),
        ('rtu_yx_info >= 8 点',            c.execute('SELECT count(*) FROM rtu_yx_info').fetchone()[0] >= 8),
        ('rtu_yk_info >= 2 点',            c.execute('SELECT count(*) FROM rtu_yk_info').fetchone()[0] >= 2),
        ('rtu_yt_info >= 3 点',            c.execute('SELECT count(*) FROM rtu_yt_info').fetchone()[0] >= 3),
        ('RTU R01/R02/R03 已注册',         len(c.execute("SELECT * FROM rtu WHERE rtu_id IN ('R01','R02','R03')").fetchall()) == 3),
        ('风机参数完整',                   c.execute("SELECT count(*) FROM device_params WHERE device='WT' AND value IS NOT NULL").fetchone()[0] == 4),
    ]
    for msg, ok in checks:
        print(f'  [{"OK" if ok else "FAIL"}] {msg}')
    if n_null > 0:
        print(f'\n[!] {n_null} 项设备参数 value=NULL,需拍板填值(柴发三参数)')
    print()

    c.close()

if __name__ == '__main__':
    main()
