import socket
import json
import time
import threading

from database import SessionLocal

from models import (
    YCInfo,
    YXInfo,
    YKInfo,
    YTInfo,
    EMSParam,
    SystemLog
)


# ============================================================
# A 端通信配置
# ============================================================

A_IP = "10.18.134.231"
A_PORT = 9000

RECONNECT_INTERVAL = 3

HEART_INTERVAL = 5

PULL_INTERVAL = 1

FULL_PULL_INTERVAL = 10


# ============================================================
# A 的点地址 → B 内部全局唯一 code
#
# A 通信：
# rtu + type + pt
#
# B 内部：
# W_SPD / WT_ACT / LD_ACT ...
# ============================================================

POINT_ADDRESS_TO_CODE = {

    # --------------------------------------------------------
    # R01 风电机组
    # --------------------------------------------------------

    ("R01", "YC", 1): "W_SPD",
    ("R01", "YC", 2): "WT_ACT",
    ("R01", "YC", 3): "WT_PITCH",
    ("R01", "YC", 4): "WT_AVAIL",
    ("R01", "YC", 5): "WT_P_SET_ACK",

    ("R01", "YX", 1): "WT_RUN",
    ("R01", "YX", 2): "WT_FAULT",
    ("R01", "YX", 3): "WIFI_STA",

    ("R01", "YK", 1): "WT_START",

    ("R01", "YT", 1): "WT_P_SET",
    ("R01", "YT", 2): "WT_PITCH_SET",


    # --------------------------------------------------------
    # R02 柴油机
    # --------------------------------------------------------

    ("R02", "YC", 1): "DG_ACT",
    ("R02", "YC", 2): "DG_P_SET_ACK",
    ("R02", "YC", 3): "DG_LOAD",

    ("R02", "YX", 1): "DG_RUN",
    ("R02", "YX", 2): "DG_ALARM",

    ("R02", "YK", 1): "DG_START",

    ("R02", "YT", 1): "DG_P_SET_CMD",


    # --------------------------------------------------------
    # R03 系统 / 负荷
    # --------------------------------------------------------

    ("R03", "YC", 1): "LD_ACT",
    ("R03", "YC", 2): "UNBAL",
    ("R03", "YC", 3): "GRID_FREQ",

    ("R03", "YX", 1): "SIM_RUN",
    ("R03", "YX", 2): "COMM_EMS",
    ("R03", "YX", 3): "COMM_WT",
}


# 反向映射
CODE_TO_POINT_ADDRESS = {
    code: address
    for address, code
    in POINT_ADDRESS_TO_CODE.items()
}


# ============================================================
# B 内部更加好理解的人话名称
# ============================================================

POINT_CODE_MAP = {

    "W_SPD": "wind_speed",
    "WT_ACT": "wind_actual_power",
    "WT_PITCH": "wind_pitch_angle",
    "WT_AVAIL": "wind_available_power",
    "WT_P_SET_ACK": "wind_setpoint_feedback",

    "WT_RUN": "wind_running",
    "WT_FAULT": "wind_fault",
    "WIFI_STA": "wind_wifi_status",

    "WT_START": "wind_start_command",

    "WT_P_SET": "wind_power_setpoint",
    "WT_PITCH_SET": "wind_pitch_setpoint",

    "DG_ACT": "diesel_actual_power",
    "DG_P_SET_ACK": "diesel_setpoint_feedback",
    "DG_LOAD": "diesel_load_rate",

    "DG_RUN": "diesel_running",
    "DG_ALARM": "diesel_alarm",

    "DG_START": "diesel_start_command",
    "DG_P_SET_CMD": "diesel_power_setpoint",

    "LD_ACT": "load_power",
    "UNBAL": "power_unbalance",
    "GRID_FREQ": "grid_frequency",

    "SIM_RUN": "simulation_status",
    "COMM_EMS": "ems_comm_status",
    "COMM_WT": "wind_comm_status",
}


class EMSIO:

    def __init__(self):

        self.sock = None

        self.connected = False

        self.running = True
        self.last_yt_send = 0

        # ====================================================
        # READY状态
        #
        # False：
        # A还没有准备好，不允许发YK/YT
        #
        # True：
        # A已经READY，可以发送控制
        # ====================================================

        self.device_ready = False

        # 上一次数据时间戳
        self.last_pull = 0
        self.last_pull_ts = 0

        # 上一次全量同步
        self.last_full_pull_time = 0
        self.registered=False
        self.last_sim_time = 0

    # ========================================================
    # 日志
    # ========================================================

    def add_log(
        self,
        level,
        message
    ):

        session = SessionLocal()

        try:

            log = SystemLog(
                level=level,
                module="operator_io",
                message=message,
                sim_time=0,
                create_time=int(
                    time.time() * 1000
                )
            )

            session.add(log)

            session.commit()

        except Exception as e:

            session.rollback()

            print(
                "日志写入失败：",
                e
            )

        finally:

            session.close()


    # ========================================================
    # 地址 → code
    # ========================================================

    def get_point_code(
        self,
        point
    ):

        address = (
            point.get("rtu"),
            point.get("type"),
            point.get("pt")
        )

        return POINT_ADDRESS_TO_CODE.get(
            address
        )


    # ========================================================
    # TCP连接
    # ========================================================

    def connect(self):

        try:

            print(
                f"正在连接 A："
                f"{A_IP}:{A_PORT}"
            )

            self.sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            self.sock.settimeout(3)

            self.sock.connect(
                (A_IP, A_PORT)
            )

            self.connected = True

            # 每次重新连接以后
            # 都重新等待READY
            self.device_ready = False

            print(
                "连接 A 成功"
            )

            self.add_log(
                "INFO",
                "EMS 与 GRID_SIM TCP连接成功"
            )

            return True

        except Exception as e:

            print(
                "连接 A 失败：",
                e
            )

            self.connected = False

            self.device_ready = False

            return False


    # ========================================================
    # 关闭连接
    # ========================================================

    def close(self):

        self.connected = False

        self.device_ready = False

        if self.sock is not None:

            try:

                self.sock.close()

            except Exception:

                pass

        self.sock = None


    # ========================================================
    # 发送 JSON Lines
    # ========================================================

    def send_json(
        self,
        obj
    ):

        if self.sock is None:

            return False

        try:

            text = json.dumps(
                obj,
                ensure_ascii=False
            ) + "\n"

            self.sock.sendall(
                text.encode("utf-8")
            )

            return True

        except Exception as e:

            print(
                "发送失败：",
                e
            )

            self.connected = False

            self.device_ready = False

            return False


    # ========================================================
    # 接收一整条 JSON Lines
    # ========================================================

    def recv_line(self):

        data = b""

        while (
            self.running
            and self.connected
        ):

            try:

                ch = self.sock.recv(1)

                if not ch:

                    raise ConnectionError(
                        "A 已关闭连接"
                    )

                if ch == b"\n":

                    break

                data += ch

                if len(data) > 4096:

                    raise ValueError(
                        "报文超过4096字节"
                    )

            except socket.timeout:

                return None

        if not data:

            return None

        text = data.decode(
            "utf-8"
        )

        return json.loads(
            text
        )


    # ========================================================
    # 注册 EMS
    # ========================================================

    def register(self):

        msg = {

            "code": "REG",

            "ts": int(
                time.time() * 1000
            ),

            "src": "EMS",

            "data": {
                "role": "EMS"
            }
        }

        if not self.send_json(msg):

            return False

        print(
            "发送报文：REG"
        )

        try:

            reply = self.recv_line()

            if reply is None:

                print(
                    "注册超时"
                )

                return False

            print(
                "收到注册回复：",
                reply
            )

            if reply.get(
                "code"
            ) == "ACK":

                print(
                    "EMS 注册成功"
                )

                self.add_log(
                    "INFO",
                    "EMS注册成功"
                )
                self.registered = True
                return True

            print(
                "EMS注册失败"
            )

            return False

        except Exception as e:

            print(
                "注册失败：",
                e
            )

            return False


    # ========================================================
    # HEART
    # ========================================================

    def send_heart(self):
        if not self.registered:
            return

        msg = {

            "code": "HEART",

            "ts": int(
                time.time() * 1000
            ),

            "src": "EMS",

            "data": {}
        }

        self.send_json(
            msg
        )


    # ========================================================
    # 增量请求
    # ========================================================
    def send_pull_delta(self):

        msg = {

            "code": "PULL_DELTA",

            "ts": int(time.time() * 1000),

            "src": "EMS",

            "data": {

                "since": self.last_pull_ts

            }

        }

        self.send_json(msg)



    # ========================================================
    # 全量请求
    # ========================================================

    def send_pull_all(self):

        msg = {

            "code": "PULL_ALL",

            "ts": int(
                time.time() * 1000
            ),

            "src": "EMS",

            "data": {}
        }

        if self.send_json(msg):

            print(
                "发送报文：PULL_ALL"
            )


    # ========================================================
    # YC 更新
    # ========================================================

    def update_yc(
        self,
        session,
        point
    ):

        point_code = self.get_point_code(
            point
        )

        if point_code is None:

            print(
                "未知YC点：",
                point
            )

            return

        obj = session.query(
            YCInfo
        ).filter(
            YCInfo.code
            == point_code
        ).first()

        if obj is None:

            print(
                f"数据库不存在YC："
                f"{point_code}"
            )

            return

        value = point.get(
            "val"
        )

        quality = point.get(
            "q",
            "GOOD"
        )

        obj.value = value

        obj.quality = (
            0
            if quality == "GOOD"
            else 1
        )

        # A的point中的ts就是仿真时间
        if "ts" in point:

            obj.sim_time = point[
                "ts"
            ]

        obj.refresh_time = int(
            time.time() * 1000
        )

        internal_name = POINT_CODE_MAP.get(
            point_code,
            point_code
        )

        print(
            f"YC更新："
            f"{point_code} "
            f"({internal_name}) "
            f"= {value}"
        )


    # ========================================================
    # YX 更新
    # ========================================================

    def update_yx(
        self,
        session,
        point
    ):

        point_code = self.get_point_code(
            point
        )

        if point_code is None:

            print(
                "未知YX点：",
                point
            )

            return

        obj = session.query(
            YXInfo
        ).filter(
            YXInfo.code
            == point_code
        ).first()

        if obj is None:

            print(
                f"数据库不存在YX："
                f"{point_code}"
            )

            return

        value = point.get(
            "val"
        )

        obj.value = value

        quality = point.get(
            "q",
            "GOOD"
        )

        obj.quality = (
            0
            if quality == "GOOD"
            else 1
        )

        if "ts" in point:

            obj.sim_time = point[
                "ts"
            ]

        obj.refresh_time = int(
            time.time() * 1000
        )

        internal_name = POINT_CODE_MAP.get(
            point_code,
            point_code
        )

        print(
            f"YX更新："
            f"{point_code} "
            f"({internal_name}) "
            f"= {value}"
        )


    # ========================================================
    # 批量更新
    # ========================================================

    def update_points(
        self,
        points
    ):

        session = SessionLocal()

        try:

            for point in points:

                point_type = point.get(
                    "type"
                )

                if point_type == "YC":

                    self.update_yc(
                        session,
                        point
                    )

                elif point_type == "YX":

                    self.update_yx(
                        session,
                        point
                    )

            session.commit()

        except Exception as e:

            session.rollback()

            print(
                "数据库更新失败：",
                e
            )

        finally:

            session.close()


    # ========================================================
    # ACK
    # ========================================================

    def handle_ack(
        self,
        msg
    ):

        data = msg.get(
            "data",
            {}
        )

        rtu = data.get(
            "rtu"
        )

        point_type = data.get(
            "type"
        )

        pt = data.get(
            "pt"
        )

        # 如果ACK没有具体点
        # 说明可能只是普通ACK
        if (
            rtu is None
            or point_type is None
            or pt is None
        ):

            return

        point_code = POINT_ADDRESS_TO_CODE.get(
            (
                rtu,
                point_type,
                pt
            )
        )

        if point_code is None:

            return

        state = data.get(
            "state",
            "OK"
        )

        session = SessionLocal()

        try:

            if point_type == "YT":

                item = session.query(
                    YTInfo
                ).filter(
                    YTInfo.code
                    == point_code
                ).first()

            elif point_type == "YK":

                item = session.query(
                    YKInfo
                ).filter(
                    YKInfo.code
                    == point_code
                ).first()

            else:

                item = None

            if item is not None:

                if state == "OK":

                    item.state = "SUCCESS"

                else:

                    item.state = "FAILED"

                session.commit()

                print(
                    f"收到ACK："
                    f"{point_code} "
                    f"{item.state}"
                )

        except Exception as e:

            session.rollback()

            print(
                "ACK处理失败：",
                e
            )

        finally:

            session.close()


    # ========================================================
    # NACK
    # ========================================================

    def handle_nack(
        self,
        msg
    ):

        data = msg.get(
            "data",
            {}
        )

        reason = data.get(
            "reason",
            "UNKNOWN"
        )

        print(
            f"收到NACK：{reason}"
        )

        # ----------------------------------------------------
        # A明确告诉我们 NOT_READY
        # 就禁止继续下发控制
        # ----------------------------------------------------

        if reason == "NOT_READY":

            self.device_ready = False

            print(
                "A当前未READY，停止下发YK/YT"
            )

        self.add_log(
            "WARN",
            f"收到NACK：{reason}"
        )


    # ========================================================
    # 处理收到的报文
    # ========================================================

    def handle_message(
        self,
        msg
    ):

        if not msg:

            return

        message_code = msg.get(
            "code"
        )


        # ====================================================
        # READY
        # ====================================================

        if message_code in ["READY","ACK"]:

            self.device_ready = True

            print(
                "=============================="
            )

            print(
                "A 已 READY"
            )

            print(
                "EMS现在允许下发YK/YT"
            )

            print(
                "=============================="
            )

            self.add_log(
                "INFO",
                "GRID_SIM READY"
            )

            return


        # ====================================================
        # 数据响应
        # ====================================================

        if message_code in (
            "RESP_DELTA",
            "RESP_ALL"
        ):

            data = msg.get(
                "data",
                {}
            )
            sim_time = data.get(
                "sim_time",
                0
            )

            print(
                "收到A端仿真时间:",
                sim_time
            )
            points = data.get(
                "points",
                []
            )
            # ===== 调试WT_AVAIL =====
            for p in points:
                if p.get("code") == "WT_AVAIL":
                   print(
                   "收到A发送的WT_AVAIL:",
                   p.get("value"
                  )
            )

            self.update_points(points)

            if msg.get("ts"):
                self.last_pull_ts = msg.get("ts")

            print(
                f"收到 {message_code}，"
                f"{len(points)} 个数据点"
            )


        # ====================================================
        # PUSH
        # ====================================================

        elif message_code == "PUSH":

            data = msg.get(
                "data",
                {}
            )

            points = data.get(
                "points",
                []
            )

            self.update_points(
                points
            )

            print(
                f"收到 PUSH，"
                f"{len(points)} 个数据点"
            )


        # ====================================================
        # ACK
        # ====================================================

        elif message_code == "ACK":

            self.handle_ack(
                msg
            )


        # ====================================================
        # NACK
        # ====================================================

        elif message_code == "NACK":

            self.handle_nack(
                msg
            )


        # ====================================================
        # HEART
        # ====================================================

        elif message_code == "HEART":

            pass


        else:

            print(
                "未知报文类型：",
                message_code
            )
            return

        # ========================================================
        # 发送YT（最终版）
        # ========================================================
    def send_yt(self, item):

        print("进入send_yt",
              item.code,
              item.value)

        address = CODE_TO_POINT_ADDRESS.get(item.code)

        if address is None:
            print("找不到YT地址:", item.code)
            return False


        rtu, point_type, pt = address


        msg = {

            "code": "YT",

            "ts": int(
                time.time() * 1000
            ),

            "src": "EMS",

            "data": {

                "rtu": rtu,

                "type": point_type,

                "pt": pt,

                "val": item.value

            }

        }
        print("准备发送YT:")
        print(msg)
        print("长度:", len(json.dumps(msg)))


        print("================")
        print("发送YT:")
        print(msg)
        print("================")



        result = self.send_json(msg)


        if result:

            print(
                "YT发送成功"
            )

        else:

            print(
                "YT发送失败"
            )


        return result
    # ========================================================
    # 发送YK
    # ========================================================
    def send_yk(
            self,
            item
    ):



        point_code = item.code

        address = CODE_TO_POINT_ADDRESS.get(
            point_code
        )

        if address is None:
            print(
                f"无法找到YK地址："
                f"{point_code}"
            )

            return False

        rtu, point_type, pt = address

        msg = {

            "code": "YK",

            "ts": int(
                time.time() * 1000
            ),

            "src": "EMS",

            "data": {

                "rtu": rtu,

                "type": point_type,

                "pt": pt,

                "val": item.value
            }
        }

        result = self.send_json(
            msg
        )

        if result:
            print(
                f"发送 YK："
                f"{point_code} "
                f"= {item.value}"
            )

        return result

    def send_pending_commands(self):
        now = time.time()
        if now - self.last_yt_send < 1:
            return

        self.last_yt_send = now
        if not self.registered:
            print("EMS未注册，跳过YK发送")
            return

        print(
            "进入send_pending_commands"
        )

        session = SessionLocal()

        try:

            mode_item = session.query(
                EMSParam
            ).filter(
                EMSParam.key ==
                "ems_control_mode"
            ).first()

            if mode_item is None:
                print(
                    "没有找到控制模式"
                )

                return

            mode = int(
                float(mode_item.value)
            )

            if mode != 1:
                print(
                    "当前开环，不发送控制"
                )

                return

            yt_list = session.query(
                YTInfo
            ).filter(
                YTInfo.state.in_(
                    [
                        "New",
                        "NEW",
                        "new"
                    ]
                )
            ).all()

            yk_list = session.query(
                YKInfo
            ).filter(
                YKInfo.state == "New"
            ).all()

            print(
                "待发送YT:",
                len(yt_list)
            )
            for item in yt_list:

                print(
                    "YT发送:",
                    item.code,
                    item.name,
                    item.value
                )

                result = self.send_yt(item)

                if result:
                    item.state = "SENT"






        except Exception as e:

            session.rollback()

            print(
                "发送YT异常:",
                e
            )


        finally:

            session.close()
            print(
                "待发送YK:",
                len(yk_list)
            )

            for item in yk_list:
                print(
                    "准备发送YK:",
                    item.code,
                    item.value
                )

                self.send_yk(item)

                item.state = "SENT"

            session.commit()


    # ========================================================
    # 接收线程
    # ========================================================

    def receive_loop(
        self
    ):

        while (
            self.running
            and self.connected
        ):

            try:

                msg = self.recv_line()

                if msg is not None:

                    self.handle_message(
                        msg
                    )

            except Exception as e:

                print(
                    "接收异常：",
                    e
                )

                self.connected = False

                self.device_ready = False

                break


    # ========================================================
    # 工作循环
    # ========================================================

    def work_loop(
        self
    ):

        last_heart = 0

        last_pull = 0

        # 每次重新连接以后
        # 先做一次全量同步


        self.send_pull_all()

        self.last_full_pull_time = time.time()

        while (
            self.running
            and self.connected

        ):

            now = time.time()


            # ------------------------------------------------
            # HEART
            # ------------------------------------------------

            if (
                now - last_heart
                >= HEART_INTERVAL
            ):

                self.send_heart()

                last_heart = now


            # ------------------------------------------------
            # 每1秒拉变化数据
            # ------------------------------------------------
            if (
                    now - self.last_pull >= PULL_INTERVAL
            ):
                self.send_pull_delta()

                self.last_pull = now
            # ------------------------------------------------
            # 发送待执行控制(YT/YK)
            # ------------------------------------------------

            self.send_pending_commands()


            # ------------------------------------------------
            # 每10秒全量同步
            # ------------------------------------------------

            if (
                now
                - self.last_full_pull_time >= FULL_PULL_INTERVAL
            ):

                self.send_pull_all()

                self.last_full_pull_time = now


            time.sleep(
                0.1
            )


    # ========================================================
    # 主程序
    # ========================================================

    def run(
        self
    ):

        print(
            "=============================="
        )

        print(
            "EMS operator_io 启动"
        )

        print(
            "=============================="
        )

        while self.running:

            # ------------------------------------------------
            # 连接
            # ------------------------------------------------

            if not self.connect():

                print(
                    f"{RECONNECT_INTERVAL}s "
                    f"后重新连接..."
                )

                time.sleep(
                    RECONNECT_INTERVAL
                )

                continue


            # ------------------------------------------------
            # 注册
            # ------------------------------------------------

            if not self.register():

                self.close()

                print(
                    f"{RECONNECT_INTERVAL}s "
                    f"后重新注册..."
                )

                time.sleep(
                    RECONNECT_INTERVAL
                )

                continue


            # ------------------------------------------------
            # 开接收线程
            # ------------------------------------------------

            receive_thread = threading.Thread(
                target=self.receive_loop,
                daemon=True
            )

            receive_thread.start()


            # ------------------------------------------------
            # 主循环
            # ------------------------------------------------

            self.work_loop()


            # ------------------------------------------------
            # 掉线
            # ------------------------------------------------

            self.close()

            print(
                f"连接断开，"
                f"{RECONNECT_INTERVAL}s "
                f"后重新连接..."
            )

            time.sleep(
                RECONNECT_INTERVAL
            )


if __name__ == "__main__":

    EMSIO().run()