import socket
import threading


def handle_client(conn, addr):
    print(f"[INFO] 新连接接入 {addr}")
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            text = data.decode('utf-8').strip()
            print(f"[RECV] {text}")

            # 如果收到 REG，回复 ACK
            if '"code":"REG"' in text:
                ack = '{"code":"ACK","src":"DEBUG_SERVER","data":{"state":"OK"}}\n'
                conn.sendall(ack.encode())
                print("[SEND] ACK (REG OK)")
            # 如果收到 YK 或 YT，也可以回复 ACK（可选）
            elif '"code":"YK"' in text or '"code":"YT"' in text:
                ack = '{"code":"ACK","src":"DEBUG_SERVER","data":{"state":"OK"}}\n'
                conn.sendall(ack.encode())
                print("[SEND] ACK (YK/YT OK)")
            else:
                # 其他消息也可以选择性回复
                print("[INFO] 收到其他消息，不回复")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        conn.close()
        print(f"[INFO] 连接关闭 {addr}")


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 9000))  # 监听所有网卡
    server.listen(5)
    print("[INFO] TCP Server 启动，端口 9000，等待连接...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == '__main__':
    main()