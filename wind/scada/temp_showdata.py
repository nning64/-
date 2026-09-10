import sqlite3

DB_PATH = "./db/wind.db"


def show_table(conn, table_name, limit=10):
    """打印指定表的前 limit 行"""
    cursor = conn.cursor()
    # 获取列名
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cursor.fetchall()]

    # 获取数据
    cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
    rows = cursor.fetchall()

    if not rows:
        print(f" 表 '{table_name}' 为空")
        return

    print(f"\n 表名: {table_name} (共{len(rows)}行, 显示最新数据)")
    # 打印表头
    print(" | ".join(columns))
    print("-" * 50)
    # 打印数据
    for row in rows:
        print(" | ".join(str(item) for item in row))


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)

    print("=" * 60)
    print("  wind.db 数据库查看器")
    print("=" * 60)

    # 1. 查看实时遥测值（最重要的数据）
    show_table(conn, "rtu_yc_info")

    # 2. 查看实时遥信值（运行状态）
    show_table(conn, "rtu_yx_info")

    # 3. 查看最新5条SCADA历史变位记录
    show_table(conn, "history_scada")

    # 4. 查看设备参数
    show_table(conn, "device_params")

    conn.close()
    print("\n 查看完毕。如果想看其他表，修改 show_table 的 table_name 参数即可。")