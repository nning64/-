import sqlite3
import os
import time

# 数据库存放路径
DB_PATH = "./db/wind.db"


def create_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    #  连接数据库（文件不存在会自动创建）
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 删除所有旧表
    print("正在清理旧表结构...")
    tables = [
        "rtu_info", "rtu_yc_info", "rtu_yx_info",
        "rtu_yt_info", "rtu_yk_info",
        "device_params", "history_control", "history_scada", "run_logs"
    ]
    for table in tables:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
    print("旧表清理完成")

    # 创建新表
    print(" 正在创建新表...")

    # 1. RTU 信息表
    cursor.execute('''
    CREATE TABLE rtu_info (
        id           INTEGER PRIMARY KEY,
        name         TEXT,
        status       INTEGER,
        ip_addr      TEXT,
        port         INTEGER,
        refresh_time INTEGER
    )
    ''')

    # 2. 遥测表（含 code 列）#real是浮点数
    cursor.execute('''
    CREATE TABLE rtu_yc_info (
        id           INTEGER PRIMARY KEY,
        code         TEXT,
        name         TEXT,
        value        REAL,    
        status       INTEGER,
        refresh_time INTEGER
    )
    ''')

    # 3. 遥信表
    cursor.execute('''
    CREATE TABLE rtu_yx_info (
        id           INTEGER PRIMARY KEY,
        code         TEXT,
        name         TEXT,
        value        INTEGER,
        status       INTEGER,
        refresh_time INTEGER
    )
    ''')

    # 4. 遥调表
    cursor.execute('''
    CREATE TABLE rtu_yt_info (
        id           INTEGER PRIMARY KEY,
        code         TEXT,
        name         TEXT,
        value        REAL,
        refresh_time INTEGER,
        ctrl_code    INTEGER
    )
    ''')

    # 5. 遥控表
    cursor.execute('''
    CREATE TABLE rtu_yk_info (
        id           INTEGER PRIMARY KEY,
        code         TEXT,
        name         TEXT,
        value        INTEGER,
        refresh_time INTEGER,
        ctrl_code    INTEGER
    )
    ''')

    # 6. 设备参数表
    cursor.execute('''
    CREATE TABLE device_params (
        id               INTEGER PRIMARY KEY CHECK (id=1),
        cut_in_wind      REAL,
        rated_wind       REAL,
        cut_out_wind     REAL,
        rated_power      REAL,
        control_mode     INTEGER,
        update_time      INTEGER
    )
    ''')

    # 7. 控制策略历史表
    cursor.execute('''
    CREATE TABLE history_control (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp        INTEGER,
        wind_speed       REAL,
        avail_power      REAL,
        output_power     REAL,
        pitch_angle      REAL,
        power_setpoint   REAL,
        run_status       INTEGER,
        control_mode     INTEGER
    )
    ''')

    # 8. SCADA 四遥历史表
    cursor.execute('''
    CREATE TABLE history_scada (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp        INTEGER,
        point_type       TEXT,
        point_id         INTEGER,
        value            REAL,
        status_or_code   INTEGER
    )
    ''')

    # 9. 系统运行日志表
    cursor.execute('''
    CREATE TABLE run_logs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp        INTEGER,
        level            TEXT,
        source           TEXT,
        message          TEXT
    )
    ''')

    # 插入初始数据
    print(" 正在插入初始数据...")

    # 1 RTU 自身信息
    cursor.execute('''
    INSERT INTO rtu_info (id, name, status, ip_addr, port, refresh_time)
    VALUES (1, '风电子站RTU', 0, '127.0.0.1', 6666, 0)
    ''')#目前是本地回环，联网时务必改为风机上位机的实际局域网ip比如 192.168.1.101

    # 3.2 遥测点位 (id, code, name, value, status, refresh_time)
    yc_data = [
        (1, 'W_SPD', '风速', 0.0, 0, 0),
        (2, 'WT_ACT', '风机有功出力', 0.0, 0, 0),
        (3, 'WT_PITCH', '当前桨距角', 0.0, 0, 0),
        (4, 'WT_AVAIL', '可用功率', 0.0, 0, 0),
        (5, 'WT_P_SET', '功率设定值', 0.0, 0, 0),
        (6, 'FREQ', '电网频率', 50.0, 0, 0)
    ]
    cursor.executemany('''
    INSERT INTO rtu_yc_info (id, code, name, value, status, refresh_time)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', yc_data)

    # 3.3 遥信点位 (id, code, name, value, status, refresh_time)
    yx_data = [
        (1, 'WT_RUN', '风机运行状态', 0, 0, 0),
        (2, 'WT_FAULT', '风机故障标志', 0, 0, 0),
        (3, 'WIFI_STA', '通信状态', 0, 0, 0)
    ]
    cursor.executemany('''
    INSERT INTO rtu_yx_info (id, code, name, value, status, refresh_time)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', yx_data)

    # 3.4 遥调点位 (id, code, name, value, refresh_time, ctrl_code)
    yt_data = [
        (1, 'WT_P_SET', '功率设定值', 0.0, 0, 0),
        (2, 'WT_PITCH_SET', '桨距角设定值', 0.0, 0, 0)
    ]
    cursor.executemany('''
    INSERT INTO rtu_yt_info (id, code, name, value, refresh_time, ctrl_code)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', yt_data)

    # 3.5 遥控点位 (id, code, name, value, refresh_time, ctrl_code)
    yk_data = [
        (1, 'WT_START', '风机启停指令', 0, 0, 0)
    ]
    cursor.executemany('''
    INSERT INTO rtu_yk_info (id, code, name, value, refresh_time, ctrl_code)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', yk_data)

    # 3.6 设备参数（默认值）
    cursor.execute('''
    INSERT INTO device_params 
    (id, cut_in_wind, rated_wind, cut_out_wind, rated_power, control_mode, update_time)
    VALUES (1, 3.0, 12.0, 25.0, 100.0, 1, ?)
    ''', (int(time.time()),))

    # 3.7 初始化日志
    cursor.execute('''
    INSERT INTO run_logs (timestamp, level, source, message)
    VALUES (?, ?, ?, ?)
    ''', (int(time.time()), 'INFO', '系统', 'wind.db 数据库初始化完成（使用 drop table 方式重建）'))

    conn.commit()
    conn.close()

    print(f" 数据库创建/重建成功: {DB_PATH}")

#  数据库操作类（包含历史记录逻辑）
class WindDB:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._last_values = {}  # 用于检测变位 {code: last_value}
        self._last_history_save = 0

        # 点位分类
        self.YC_CODES = ['W_SPD', 'WT_ACT', 'WT_PITCH', 'WT_AVAIL', 'WT_P_SET']
        self.YX_CODES = ['WT_RUN']

    def _execute(self, sql, params=()):
        try:
            self.cursor.execute(sql, params)
            self.conn.commit()
            return True
        except Exception as e:
            print(f"[DB错误] {e}")
            return False

    def update_yc(self, code, val, grid_time, status=0):
        """更新遥测，并自动检测变位写历史"""

        last_val = self._last_values.get(code)
        # 变位检测（阈值0.01）
        is_changed = (last_val is None or abs(val - last_val) > 0.01)

        sql = "UPDATE rtu_yc_info SET value=?, refresh_time=? WHERE code=?"
        if self._execute(sql, (val, int(grid_time), code)):
            self._last_values[code] = val
            if is_changed:
                self._insert_scada_history('YC', code, val, status, grid_time)
            return True
        return False

    def update_yx(self, code, raw_val, grid_time, status=0):
        """更新遥信，并自动检测变位写历史"""
        val = int(raw_val)
        last_val = self._last_values.get(code)
        is_changed = (last_val is None or val != last_val)

        sql = "UPDATE rtu_yx_info SET value=?, refresh_time=? WHERE code=?"
        if self._execute(sql, (val, int(grid_time), code)):
            self._last_values[code] = val
            if is_changed:
                self._insert_scada_history('YX', code, val, status, grid_time)
            return True
        return False
    def update_yt(self, code, val, grid_time, ctrl_code=1):
        """更新遥调（接收单片机 YT 回传，如桨距角设定值）"""
        sql = "UPDATE rtu_yt_info SET value=?, refresh_time=?, ctrl_code=? WHERE code=?"
        return self._execute(sql, (float(val), int(grid_time), ctrl_code, code))

    def update_yk(self, code, val, grid_time, ctrl_code=1):
        """更新遥控（接收单片机 YK 回传，如启停指令状态）"""
        sql = "UPDATE rtu_yk_info SET value=?, refresh_time=?, ctrl_code=? WHERE code=?"
        return self._execute(sql, (int(val), int(grid_time), ctrl_code, code))

    def insert_log(self, level, source, message):
        """写系统运行日志表"""
        sql = "INSERT INTO run_logs (timestamp, level, source, message) VALUES (?, ?, ?, ?)"
        return self._execute(sql, (int(time.time()), level, source, message))

    def _insert_scada_history(self, point_type, code, value, status, grid_time):
        """记录四遥变位历史"""
        table_map = {'YC': 'rtu_yc_info', 'YX': 'rtu_yx_info'}
        table = table_map.get(point_type)
        if not table:
            return
        # 查点号
        self.cursor.execute(f"SELECT id FROM {table} WHERE code=?", (code,))
        row = self.cursor.fetchone()
        if not row:
            return
        point_id = row[0]
        sql = """
        INSERT INTO history_scada (timestamp, point_type, point_id, value, status_or_code)
        VALUES (?, ?, ?, ?, ?)
        """
        self.cursor.execute(sql, (int(grid_time), point_type, point_id, float(value), int(status)))
        self.conn.commit()

    def max_history_timestamp(self):
        """返回 history_control 里最大的 timestamp（秒），无数据返回 None

        供 SerialReceiverThread 启动时读取：作为 session_offset 的初始
        基准（叠加 RESET_GAP_MS），避免 wind_gui 重启后新写入的 timestamp
        与 DB 已有数据冲突。"""
        try:
            self.cursor.execute("SELECT MAX(timestamp) FROM history_control")
            row = self.cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else None
        except Exception as e:
            print(f"[max_history_timestamp] 读取失败: {e}")
            return None

    def save_control_snapshot(self, grid_time):
        """保存控制策略快照（每5秒执行一次）

        注意：grid_time 应当已经是经过 session_offset 校正过的"绝对仿真
        秒"——SerialReceiverThread 在检测到单片机 HAL_GetTick 回退时
        自动累加偏移。这样写入的 timestamp 永远单调递增，GUI 端按
        timestamp 排序画曲线就不会因为单片机复位而"消失"在旧值处。
        """
        try:
            ts = int(grid_time)

            # 从数据库读取当前最新值
            def get_val(code):
                self.cursor.execute("SELECT value FROM rtu_yc_info WHERE code=?", (code,))
                row = self.cursor.fetchone()
                return row[0] if row else 0.0

            def get_yx(code):
                self.cursor.execute("SELECT value FROM rtu_yx_info WHERE code=?", (code,))
                row = self.cursor.fetchone()
                return row[0] if row else 0

            wind = get_val('W_SPD')
            avail = get_val('WT_AVAIL')
            output = get_val('WT_ACT')
            pitch = get_val('WT_PITCH')
            set_power = get_val('WT_P_SET')
            run_status = get_yx('WT_RUN')

            sql = """
            INSERT INTO history_control
            (timestamp, wind_speed, avail_power, output_power, pitch_angle, power_setpoint, run_status, control_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            # 控制模式默认为1（闭环），因为我们已在接收真实数据
            self.cursor.execute(sql, (ts, wind, avail, output, pitch, set_power, int(run_status), 1))
            self.conn.commit()
        except Exception as e:
            print(f"保存历史快照失败: {e}")

    def get_current_value(self, code):
        """快速获取当前值（用于打印）"""
        self.cursor.execute("SELECT value FROM rtu_yc_info WHERE code=?", (code,))
        row = self.cursor.fetchone()
        if row:
            return row[0]
        self.cursor.execute("SELECT value FROM rtu_yx_info WHERE code=?", (code,))
        row = self.cursor.fetchone()
        return row[0] if row else 0

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    create_database()