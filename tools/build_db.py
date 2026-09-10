"""
build_db.py - 一键重建 grid.db (从 config/schema.sql)

用法 (项目根目录):
    python tools/build_db.py

行为:
- 检测 grid.db 是否存在,存在则询问保留 / 重置
- 读取 config/schema.sql,执行全部建表 + 种子
- 完毕自动跑一次 verify_db 打印验收清单
"""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB   = ROOT / 'grid.db'
SCHEMA = ROOT / 'config' / 'schema.sql'

def ask_yesno(prompt):
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        # 管道调用 / 无终端,默认跳过重建(更安全)
        return False
    return ans == 'y'

def main():
    if not SCHEMA.exists():
        print(f'[X] 找不到 schema: {SCHEMA}')
        sys.exit(1)

    if DB.exists():
        print(f'[!] {DB} 已存在 ({DB.stat().st_size:,} bytes)')
        if not ask_yesno('    输入 y 重建 / 回车跳过: '):
            print('    跳过建库,直接验证:')
        else:
            DB.unlink()
            print('    已删除旧 db')
    else:
        print(f'[OK] db 不存在,准备创建 {DB}')

    if not DB.exists():
        # 重新建
        sql = SCHEMA.read_text(encoding='utf-8')
        c = sqlite3.connect(str(DB))
        c.executescript(sql)
        c.commit()
        c.close()
        print(f'[OK] 已创建 {DB} ({DB.stat().st_size:,} bytes)')

    print()
    # 自动调一次 verify
    sys.path.insert(0, str(HERE))
    import verify_db
    verify_db.main()

if __name__ == '__main__':
    main()
