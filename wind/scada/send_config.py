import serial
import json
import time

# 配置串口（与之前一致）
SERIAL_PORT = "COM3"   # 改成你的实际端口
BAUDRATE = 115200

def send_params(port, cut_in, rated_wind, cut_out, rated_power, mode):
    """
    发送参数配置到 STM32
    mode: 0 开环, 1 闭环
    """
    cmd = {
        "cmd": "set_params",
        "cut_in": cut_in,
        "rated_wind": rated_wind,
        "cut_out": cut_out,
        "rated_power": rated_power,
        "mode": mode
    }
    json_str = json.dumps(cmd) + "\n"   # 注意末尾换行

    try:
        ser = serial.Serial(port, BAUDRATE, timeout=1)
        ser.write(json_str.encode())
        # 等待回复，最多尝试 3 秒
        timeout = 3.0
        start_time = time.time()
        while time.time() - start_time < timeout:
            line = ser.readline().decode().strip()
            if not line:
                continue

            # 固件串口是多路复用输出，除了参数应答还混着：
            #   - {"code":...} 遥测/心跳镜像帧
            #   - "ACK OK" / "REG OK" / "LINK LOST" 等状态文本行
            #   - 串口分包截断的半截 JSON 碎片（如 'pe":"YK",...}}'）
            # 所以这里用白名单：只有 OK / ERROR / UNKNOWN_CMD 是应答，
            # 其余一律当噪声忽略，靠超时兜底（绝不因噪声误报失败）。
            if line == "OK":
                print(" 参数设置成功！")
                ser.close()
                return True
            elif line == "ERROR":
                print(" 单片机解析失败（JSON格式错误）")
                ser.close()
                return False
            elif line == "UNKNOWN_CMD":
                print(" 未知命令，请检查 JSON 格式")
                ser.close()
                return False
            else:
                if line.startswith('{') or '"code"' in line or '}}' in line:
                    print(f" 忽略遥测数据: {line[:50]}...")
                else:
                    print(f" 忽略状态行: {line}")
                continue

        # 超时
        print(" 超时：未收到 OK 回复")
        ser.close()
        return False

    except Exception as e:
        print(f" 发送失败: {e}")
        return False
if __name__ == "__main__":
    # 示例：修改切入风速为 2.5，额定风速 11.0，切出 25.0，额定功率 90.0，闭环模式
    send_params(SERIAL_PORT, 2.5, 11.0, 25.0, 90.0, 1)