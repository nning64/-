"""
diagnose_ems_conn.py - EMS 连不上? 一键诊断

用法 (项目根目录):
    python tools/diagnose_ems_conn.py [host] [port]

行为:
  1. 读 grid.db 看 R03:2 COMM_EMS / R03:3 COMM_WT 当前状态(谁在线)
  2. 检查本机 9000 端口是否在 LISTEN
  3. 主动拨号模拟 EMS 注册, 看 A 总机怎么回
  4. 主动拨号模拟 WT_CTRL 注册, 看 A 总机怎么回
  5. 汇总判定: 端口不通 / 注册被拒 / 一切正常

注: A 总机在 comm=True 时监听 0.0.0.0:9000. 若 main.py 没起或 --no-comm
    启动, 端口空着 —— 第一关就会被踢回。
"""
import os, sys, sqlite3, socket, json, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB = ROOT / 'grid.db'

def _db_yx(rtu, pt):
    try:
        c = sqlite3.connect(str(DB), timeout=1)
        r = c.execute("SELECT value FROM yx_realtime WHERE rtu_id=? AND point_no=?",
                      (rtu, pt)).fetchone()
        c.close()
        return r[0] if r else None
    except Exception:
        return None

def _port_listening(host, port):
    """不真连 —— 用 connect_ex 能连上就说明有东西在听; 同时强制 IPv4。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()

def _try_reg(host, port, role, timeout=2.0):
    """真拨号, 发 REG, 收 ACK/NACK 一帧. 返回 ('ACK'|'NACK'|'TIMEOUT'|'CONN_REFUSED', detail)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
    except (ConnectionRefusedError, socket.timeout) as e:
        return 'CONN_REFUSED', str(e)
    try:
        frame = {'code': 'REG', 'ts': int(time.time()*1000), 'src': role,
                 'data': {'role': role}}
        s.sendall((json.dumps(frame) + '\n').encode('utf-8'))
        s.settimeout(timeout)
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf.strip():
            return 'EMPTY', '连接成功但没收到任何一帧'
        try:
            obj = json.loads(buf.decode('utf-8').strip().split('\n')[0])
            return obj.get('code', 'UNKNOWN'), obj
        except Exception as e:
            return 'PARSE_ERR', f'收到非 JSON: {buf[:60]!r}'
    except socket.timeout:
        return 'TIMEOUT', '2 秒内没回'
    finally:
        try: s.close()
        except: pass

def main():
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000

    print('=' * 60)
    print(f'  A 总机诊断 (目标 {host}:{port})')
    print('=' * 60)

    # 1) DB 里看通信状态灯
    if not DB.exists():
        print(f'\n[1] grid.db 不存在 ({DB})')
        print('    先 python tools/build_db.py 建库')
        sys.exit(1)
    ems_on = _db_yx('R03', 2)
    wt_on  = _db_yx('R03', 3)
    ems_s = '在线' if ems_on == 1 else '离线'
    wt_s  = '在线' if wt_on  == 1 else '离线'
    print('\n[1] grid.db 里通信状态灯 (A 总机每 1s 刷一次):')
    print(f'    R03:2 COMM_EMS = {ems_on}  ({ems_s})')
    print(f'    R03:3 COMM_WT  = {wt_on}  ({wt_s})')
    if ems_on == 1:
        print('    !! 注意: A 视角 EMS 在线 —— 如果你这边连不上, 多半是')
        print('       别的 EMS 进程占了 role, 见 §3 同 role 互踢机制')

    # 2) 端口监听?
    listening = _port_listening(host, port)
    print(f'\n[2] {host}:{port} 端口: {"在听 ✓" if listening else "不在听 ✗"}')
    if not listening:
        print('    可能原因:')
        print('    a) main.py 没起 / 起了但用 --no-comm')
        print('    b) main.py 起了但 comm 总机那行抛 OSError (端口被占) 后退出')
        print('       —— 看 main.py 控制台有无 "[X] 通讯总机启动失败"')
        print('    c) 主机/端口错了 (你连的不是 A 这台机器)')
        sys.exit(1)

    # 3) 模拟 EMS 注册
    print('\n[3] 模拟 EMS 拨号注册:')
    code, detail = _try_reg(host, port, 'EMS')
    if code == 'ACK':
        print('    ✓ 收到 ACK  — EMS 注册成功!')
    elif code == 'NACK':
        reason = detail.get('data', {}).get('reason') if isinstance(detail, dict) else '?'
        print(f'    ✗ 收到 NACK  reason={reason!r}')
        print('      可能原因: BAD_SRC  (data.role 不是 "EMS" —— src 字段也得不等于 EMS 才能过)')
        print('      不允许的 role: ' + str(['"EMS" 不在', 'role 拼写错']))
    elif code == 'CONN_REFUSED':
        print('    ✗ 连不上 (端口空着/超时) — 见 [2]')
    else:
        print(f'    ? {code}: {detail}')

    # 4) 模拟 WT_CTRL 注册
    print('\n[4] 模拟 WT_CTRL 拨号注册:')
    code, detail = _try_reg(host, port, 'WT_CTRL')
    if code == 'ACK':
        print('    ✓ 收到 ACK  — WT_CTRL 注册成功!')
    elif code == 'CONN_REFUSED':
        print('    ✗ 连不上 (端口空着/超时) — 见 [2]')
    else:
        print(f'    ? {code}: {detail}')

    print('\n' + '=' * 60)
    print('  排查小结')
    print('=' * 60)
    print('• [2] 不在听  →  A 总机没起, 重启 main.py')
    print('• [3] EMS 收 NACK  →  B 端 REG 帧 src 或 data.role 不等于 "EMS"')
    print('• [3] EMS 连不上但 [4] WT_CTRL 收 ACK  →  src/role 字段问题')
    print('• [1] 显示 EMS 在线  →  别的 EMS 已经占了角色, 你的连不上是因为')
    print('                       A 同 role 只留最新, 旧连接被踢 (comm/server.py:568)')

if __name__ == '__main__':
    main()