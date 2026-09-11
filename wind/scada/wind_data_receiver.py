import serial
import serial.tools.list_ports
import json
import time
import sys
from wind_db import WindDB

#  配置
BAUDRATE = 115200
HISTORY_SAVE_INTERVAL = 1.0  # 每1秒保存一次控制策略快照（与 PUSH 帧同频）
SERIAL_PORT = "COM3"  # 串口号


# ---------- 点表映射 ----------
def map_point_to_code(rtu, ptype, pt):
    """将 (RTU, 类别, 点号) 映射为数据库 code"""
    # 只处理 R01（风电机组），其他 RTU 暂时忽略或打日志
    if rtu != "R01":
        return None

    # R01 的点表映射（与单片机协议一致）
    if ptype == "YC":
        if pt == 1: return "W_SPD"       # 风速
        if pt == 2: return "WT_ACT"      # 实际输出功率
        if pt == 3: return "WT_PITCH"    # 当前桨距角
        if pt == 4: return "WT_AVAIL"    # 可用功率
        if pt == 5: return "WT_P_SET"    # 功率设定值
    elif ptype == "YX":
        if pt == 1: return "WT_RUN"      # 运行状态
        if pt == 2: return "WT_FAULT"    # 故障标志
        if pt == 3: return "WIFI_STA"    # 通信状态
    elif ptype == "YT":
        if pt == 1: return "WT_P_SET"    # 功率设定值（遥调）
        if pt == 2: return "WT_PITCH_SET"  # 桨距角设定值（遥调）
    elif ptype == "YK":
        if pt == 1: return "WT_START"    # 启停指令（遥控）
    return None


# ---------- 文本状态行映射表 ----------
# 单片机在 WiFi 初始化和透传维护期间会往串口打纯文本状态行（\r\n 结尾）。
# 每项: (行前缀, 日志级别, 中文提示, WIFI_STA 通信状态或 None 表示不变)
# 注意前缀按最长优先排列，避免 "AT OK" 抢先匹配 "AT FAIL" 之类的问题。
STATUS_LINES = [
    ("STM32 Ready",     "INFO",  "STM32 已就绪，开始初始化 WiFi", 0),
    ("WiFi Init",       "INFO",  "正在初始化 ESP8266 / WiFi", 0),
    ("AT OK",           "INFO",  "ESP8266 AT 应答正常", None),
    ("AT FAIL",         "ERROR", "ESP8266 AT 无应答", 0),
    ("CWMODE FAIL",     "ERROR", "设置 ESP8266 工作模式失败", 0),
    ("CIPSEND failed",  "ERROR", "CIPSEND 发送失败", 0),
    ("WiFi OK",         "INFO",  "WiFi 已接入路由器", None),
    ("WiFi FAIL",       "ERROR", "WiFi 接入路由器失败", 0),
    ("TCP OK",          "INFO",  "TCP 已连接服务器", None),
    ("TCP FAIL",        "ERROR", "TCP 连接服务器失败", 0),
    ("REG OK",          "INFO",  "已在服务器完成注册", 1),
    ("REG FAIL",        "ERROR", "服务器注册失败", 0),
    ("ACK received",    "INFO",  "收到服务器 ACK（注册成功）", None),
    ("ACK timeout",     "WARN",  "等待服务器 ACK 超时", None),
    ("ACK late",        "WARN",  "YK/YT 指令超时，已重发一次", None),
    ("ACK TIMEOUT",     "ERROR", "指令重发仍无 ACK，已放弃", 0),
    ("ACK OK",          "INFO",  "YK/YT 指令已被服务器确认", None),
    ("NACK",            "WARN",  "服务器拒绝指令 (NACK)", None),
    ("Transparent Mode", "INFO", "透传模式已建立，通信链路就绪", 1),
    ("LINK LOST",       "ERROR", "与服务器链路断开，正在重连并重新注册", 0),
    ("RECONNECT OK",    "INFO",  "链路已恢复并完成重新注册", 1),
    ("RECONNECT FAIL",  "ERROR", "重连失败，将持续重试", 0),
    ("TCP -> ",           "INFO",  "正在连接 TCP 服务器（目标 IP:端口见串口原始行）", None),
    ("CIPSTART reply",    "ERROR", "CIPSTART 被拒：服务器端口未监听或被防火墙拦截", 0),
    ("CIPSTART: no reply", "ERROR", "CIPSTART 无回复：服务器 IP 不通或不在同一网段（IP 变了？）", 0),
    ("INIT FAIL",         "ERROR", "初始化失败，已转入后台持续重试（此时风机停机，遥测保持不变）", 0),
    ("A PUSH point",     "INFO",  "收到 A 推送的测点（点号见串口原始行，用于核对点表）", None),
    ("PARSE_ERR",       "WARN",  "服务器下发的帧解析失败", None),
]


# ---------- 共享解析器（wind_gui.py 也调用本函数） ----------
def _apply_point(db, point, grid_time):
    """把一个测点写进数据库。兼容新旧两种 val 编码：
    - 新固件: val 为浮点数，无 scale 字段
    - 旧固件: val 为整数 + scale 缩放因子
    """
    rtu = point.get("rtu")
    ptype = point.get("type")
    pt = point.get("pt")
    val = point.get("val")
    if val is None:          # 坏数据点（q:BAD）val 为 null，直接丢弃
        return None
    scale = point.get("scale", 1)
    real_val = val / scale if scale and scale != 1 else val

    code = map_point_to_code(rtu, ptype, pt)
    if code is None:
        return None

    if ptype == "YC":
        db.update_yc(code, float(real_val), grid_time)
    elif ptype == "YX":
        db.update_yx(code, int(real_val), grid_time)
    elif ptype == "YT":
        db.update_yt(code, real_val, grid_time)
    elif ptype == "YK":
        db.update_yk(code, real_val, grid_time)
    return code


def parse_stm32_line(line, db, status_cb=None):
    """解析一行单片机（USART2）输出。

    单片机新固件会输出两类内容（都按行分割）：
      1. 纯文本状态行: STM32 Ready / WiFi Init... / AT OK / WiFi OK /
         TCP OK / REG OK / ACK received! / Transparent Mode / 各种 FAIL...
      2. JSON 帧（JSON Lines）:
         - REG / YK / YT: data 是单个测点对象（不是数组），val 为浮点
         - PUSH: data.points 数组，本地遥测镜像（风速/功率/桨距角等）

    返回 (kind, payload):
      kind == 'status' -> payload = (level, message)   文本状态行
      kind == 'json'   -> payload = code 字符串        JSON 帧
      kind == 'ignore' -> payload = None               无法识别/无需处理

    status_cb(level, message): 每条文本状态行回调一次（GUI 用它刷新状态栏）。
    """
    line = line.strip()
    if not line:
        return "ignore", None

    # ---- JSON 帧 ----
    if line.startswith("{"):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            msg = "收到无法解析的帧: " + line[:40]
            db.insert_log("WARN", "STM32", msg)
            return "status", ("WARN", msg)

        code = data.get("code")
        ts = data.get("ts", 0)
        grid_time = ts / 1000.0 if ts else 0
        d = data.get("data") or {}

        if code in ("YK", "YT"):
            # 单点帧: data 本身就是一个 Point（rtu/type/pt/val）
            _apply_point(db, d, grid_time)
        elif isinstance(d, dict) and "points" in d:
            # 数组帧: 本地遥测镜像 PUSH
            for point in d["points"]:
                _apply_point(db, point, grid_time)
        elif code == "REG":
            db.insert_log("INFO", "STM32", "已向服务器发送注册帧")
        # HEART 只发服务器不镜像到串口，一般收不到；收到也不需要处理
        return "json", code

    # ---- 文本状态行 ----
    # 特判 "TCP -> ip:port"：目标地址是运行时变量（上位机 set_server 可改），
    # 静态映射表携带不了——单独解析，把实际地址带进状态消息，
    # 界面"当前服务器"行才能显示固件此刻正在连哪个地址（IP 重连的直接证据）。
    if line.startswith("TCP -> "):
        msg = "正在连接 TCP 服务器 " + line[len("TCP -> "):].strip()
        db.insert_log("INFO", "STM32", msg)
        if status_cb:
            status_cb("INFO", msg)
        return "status", ("INFO", msg)

    for prefix, level, message, wifi in STATUS_LINES:
        if line.startswith(prefix):
            db.insert_log(level, "STM32", message)
            if wifi is not None:
                db.update_yx("WIFI_STA", wifi, time.time())
            if status_cb:
                status_cb(level, message)
            return "status", (level, message)

    # 其余未知文本（如配置命令的 OK/ERROR/UNKNOWN_CMD 回执）静默忽略
    return "ignore", None


# ---------- 独立运行模式 ----------
if __name__ == "__main__":

    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
        print("串口打开成功\n")
    except Exception as e:
        print(f"串口打开失败: {e}")
        sys.exit(1)

    # 2. 初始化数据库
    db = WindDB()
    print("数据库连接成功")

    def on_status(level, message):
        print(f"  [{level:>5}] {message}")

    try:
        last_grid_time = 0
        while True:
            line = ser.readline().decode("utf-8", errors="replace")
            if not line.strip():
                continue

            kind, payload = parse_stm32_line(line, db, status_cb=on_status)

            if kind == "json" and payload == "PUSH":
                # 用帧内时间戳作为仿真时刻
                ts = json.loads(line).get("ts", 0)
                last_grid_time = ts / 1000.0 if ts else last_grid_time

                # 每5秒保存一次控制策略快照
                current_time = time.time()
                if current_time - db._last_history_save >= HISTORY_SAVE_INTERVAL:
                    db.save_control_snapshot(last_grid_time)
                    db._last_history_save = current_time

                # 打印当前状态
                wind = db.get_current_value("W_SPD")
                power = db.get_current_value("WT_ACT")
                run = int(db.get_current_value("WT_RUN"))
                print(f" t={last_grid_time:>6.0f}s | 风速={wind:>5.2f}m/s | "
                      f"功率={power:>6.1f}kW | 运行{'成功' if run else '停止'}")

    except KeyboardInterrupt:
        print("\n 用户中断")
    finally:
        ser.close()
        db.close()
        print(" 已关闭")
