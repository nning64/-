"""
comm/server.py - A(电网模拟器)的 TCP 总机 (P2-1: 骨架)

大白话: A 是"总机"。B(EMS) 和 C(风机控制器) 各自拨一条"电话线"进来,
都打在同一个号码 0.0.0.0:9000 上。总机按注册时自报的身份(role)分辨谁是谁。

协议: interface.md v0.3 的 JSON Lines(一行一帧, UTF-8, 换行结尾)。

P2-1: 能连、能注册、能心跳、能踢人。
P2-2: 会"答数据题"了:
  - EMS 发 PULL_ALL  -> A 从公告栏(yc/yx_realtime)抄 19 点全量回 RESP_ALL
  - EMS 发 PULL_DELTA(since毫秒) -> A 从变位账本(yc/yx_history)翻 since 之后的点回 RESP_DELTA
  - A 每 1s 主动给 WT_CTRL 推 PUSH(风速 W_SPD + 功率设定 WT_P_SET)
P2-3: 会"收单"了 (指令链):
  - EMS/风机控制器 发 YK/YT -> 总机只做"验单 + 排队":
      ① 点存在? ② 值类型对? ③ 值在量程内?
    -> 合格: 写 yk_realtime/yt_realtime(state=0 待执行) + 回 ACK
    -> 不合格: 回 NACK(REJECT + reason), 不写库
  - "动手执行"由 main.py 仿真主循环做(每秒扫 state=0 喂给风机/柴发模型再置 state=1)
  - 总机维护三个"通信状态灯" YX 点: R01:3 WIFI_STA / R03:2 COMM_EMS / R03:3 COMM_WT
    按 EMS/WT_CTRL 是否在线置 0/1 (main.py 不再覆盖这三个点)
  - PUSH 仍只允许 A->C 单向; 谁给 A 发 PUSH 视为方向用错回 NACK(NOT_ALLOWED)

时间口径: 报文里 Point 的 ts = 仿真秒 sim_time*1000 (毫秒), A 是唯一时钟源。
  PULL_DELTA 的 since 也是仿真毫秒。见 interface.md §4.4 / §5.4。

单独测试总机(命令行):
    cd 大作业
    python -m comm.server            # 监听 0.0.0.0:9000, Ctrl+C 停
"""

import argparse
import json
import selectors
import socket
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB   = ROOT / 'grid.db'
sys.path.insert(0, str(ROOT))
from core.syslog import log as db_log   # 把关键事件写进 log 表(UI 运行日志)

MAX_FRAME = 4096        # 单帧上限(字节), 见 interface.md §4.1
HEART_PERIOD = 5.0      # A 主动广播心跳的周期(秒)
PUSH_PERIOD = 1.0       # A 给 C(WT_CTRL)推 PUSH 的周期(秒), C 的采集周期就是 1s
OFFLINE_TIMEOUT = 15.0  # 连续无报文判离线的秒数

VALID_ROLES = ('EMS', 'WT_CTRL')


def encode(obj):
    """对象 -> 一行 JSON + 换行 的字节流。超过单帧上限抛 ValueError。"""
    text = json.dumps(obj, ensure_ascii=False)
    if len(text.encode('utf-8')) > MAX_FRAME:
        raise ValueError('frame too big')
    return (text + '\n').encode('utf-8')


def now_ms():
    return int(time.time() * 1000)


def _ts_now():
    return time.strftime('%H:%M:%S')


class GridSimServer:
    """A 端总机: 非阻塞 selectors 单线程同时管多条连接。"""

    def __init__(self, host='0.0.0.0', port=9000, db=None,
                 heart_period=HEART_PERIOD, push_period=PUSH_PERIOD,
                 offline_timeout=OFFLINE_TIMEOUT):
        self.db = str(db or DB)
        self.heart_period = heart_period
        self.push_period = push_period
        self.offline_timeout = offline_timeout

        self.sel = selectors.DefaultSelector()
        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.setblocking(False)
        self.lsock.bind((host, port))
        self.lsock.listen(4)
        self.sel.register(self.lsock, selectors.EVENT_READ, self._on_accept)

        # fd -> {sock, addr, role(None=未注册), buf, last_recv}
        self.conns = {}
        self.role_fd = {}   # role -> fd (同一 role 只留一个)
        self._last_heart = 0.0
        self._last_push = 0.0
        self._running = True
        # "指令入队"日志去重: B/C 常每秒重发同一设定值, 同一条指令 5 秒内
        # 只写一次 log 表(终端仍每次都打印)。(2026-09-11)
        self._cmd_sig = None
        self._cmd_sig_t = 0.0

    # ---------- 小工具 ----------
    def log(self, level, msg, to_db=True):
        """打日志。level=DEBUG 或 to_db=False 时只打印终端, 不写 log 表。

        to_db=False 用于"内容完全相同的重复事件"(如 B/C 每秒重发同一个设定
        值): 终端照常打印看得到活动, 但不去灌 log 表 —— 之前 20 分钟就能往
        库里堆 2 万条一模一样的"指令入队"记录。(2026-09-11)
        """
        print('[%s] [%-5s] %s' % (_ts_now(), level, msg), flush=True)
        if level != 'DEBUG' and to_db:       # DEBUG 心跳那种高频噪音不入库
            db_log(level, 'SERVER', msg, db=self.db)

    def _read_sim_time(self):
        """从 db 读当前仿真秒(给心跳广播用)。读不到就 -1, 不抛异常。"""
        try:
            con = sqlite3.connect(self.db)
            try:
                row = con.execute(
                    "SELECT value FROM sim_params WHERE key='sim_time'"
                ).fetchone()
                return int(row[0]) if row else -1
            finally:
                con.close()
        except Exception:
            return -1

    # ---------- P2-2: 从 db 读数据应答 (仿真毫秒时间口径) ----------
    def _sim_ms(self):
        """当前仿真毫秒 = sim_time*1000。读不到 sim_time 给 0。"""
        st = self._read_sim_time()
        return max(0, st) * 1000

    def _open(self):
        """开一条带 busy_timeout 的只读连接(WAL 模式下与 main.py 并发安全)。"""
        con = sqlite3.connect(self.db, timeout=5)
        con.execute('PRAGMA busy_timeout=5000')
        return con

    @staticmethod
    def _pt(rtu, typ, pt, val, q=None, ts_ms=0):
        """按 interface.md §4.4 拼一个 Point。YC 坏值时 q='BAD' 且 val=null。"""
        p = {'rtu': rtu, 'type': typ, 'pt': pt, 'val': val}
        if q is not None:
            p['q'] = q
        p['ts'] = ts_ms
        return p

    def _snapshot_all(self):
        """PULL_ALL 用: 从公告栏抄 11 个 YC + 8 个 YX 的当前值。"""
        sim = self._sim_ms()
        points = []
        try:
            con = self._open()
            try:
                for rtu, pt, val, q in con.execute(
                    'SELECT rtu_id, point_no, value, quality '
                    'FROM yc_realtime ORDER BY rtu_id, point_no'
                ):
                    if val is None or q != 0:      # 坏值: val 置 null + q=BAD
                        points.append(self._pt(rtu, 'YC', pt, None, 'BAD', sim))
                    else:
                        points.append(self._pt(rtu, 'YC', pt, val, 'GOOD', sim))
                for rtu, pt, val in con.execute(
                    'SELECT rtu_id, point_no, value '
                    'FROM yx_realtime ORDER BY rtu_id, point_no'
                ):
                    points.append(self._pt(rtu, 'YX', pt, val, None, sim))
            finally:
                con.close()
        except Exception as e:
            self.log('ERROR', '快照读取失败: %s' % e)
        return points

    def _delta_since(self, since_ms):
        """PULL_DELTA 用: 从变位账本翻 sim_time*1000 > since 的记录。"""
        points = []
        try:
            con = self._open()
            try:
                rows = con.execute(
                    'SELECT rtu_id, point_no, sim_time, value FROM yc_history '
                    'WHERE sim_time*1000 > ? ORDER BY sim_time, id',
                    (since_ms,),
                ).fetchall()
                for rtu, pt, st, val in rows:
                    points.append(self._pt(rtu, 'YC', pt, val, 'GOOD', st * 1000))
                rows = con.execute(
                    'SELECT rtu_id, point_no, sim_time, value FROM yx_history '
                    'WHERE sim_time*1000 > ? ORDER BY sim_time, id',
                    (since_ms,),
                ).fetchall()
                for rtu, pt, st, val in rows:
                    points.append(self._pt(rtu, 'YX', pt, val, None, st * 1000))
            finally:
                con.close()
        except Exception as e:
            self.log('ERROR', '变位读取失败: %s' % e)
        return points

    def _read_yc(self, rtu, pt):
        """读公告栏某个 YC 点的当前值。没有就返回 None。"""
        try:
            con = self._open()
            try:
                row = con.execute(
                    'SELECT value, quality FROM yc_realtime '
                    'WHERE rtu_id=? AND point_no=?', (rtu, pt),
                ).fetchone()
                if not row or row[1] != 0:
                    return None
                return row[0]
            finally:
                con.close()
        except Exception:
            return None

    def _read_wt_p_set(self):
        """PUSH 的功率设定: 有 EMS 遥调指令(yt_realtime R01 pt1)用指令值,
        还没有就用风机额定功率 100(表示'没人限发, 按能力满发')。"""
        try:
            con = self._open()
            try:
                row = con.execute(
                    "SELECT value FROM yt_realtime "
                    "WHERE rtu_id='R01' AND point_no=1"
                ).fetchone()
                if row and row[0] is not None:
                    return float(row[0])
                row = con.execute(
                    "SELECT value FROM device_params "
                    "WHERE device='WT' AND key='rated_power'"
                ).fetchone()
                return float(row[0]) if row else 100.0
            finally:
                con.close()
        except Exception:
            return 100.0

    def _push_to_wt(self):
        """每 1s 给风机控制器推一次 PUSH: 风速 W_SPD + 功率设定 WT_P_SET。"""
        fd = self.role_fd.get('WT_CTRL')
        conn = self.conns.get(fd) if fd is not None else None
        if conn is None:
            return
        sim = self._sim_ms()
        wind = self._read_yc('R01', 1)
        p_set = self._read_wt_p_set()
        pts = []
        if wind is None:                       # 坏值也如实推, 让 C 知道通道没数据
            pts.append(self._pt('R01', 'YC', 1, None, 'BAD', sim))
        else:
            pts.append(self._pt('R01', 'YC', 1, wind, 'GOOD', sim))
        pts.append(self._pt('R01', 'YT', 1, p_set, None, sim))   # 见示例⑪
        self._send(conn, {'code': 'PUSH', 'ts': now_ms(), 'src': 'GRID_SIM',
                          'data': {'points': pts}})

    def _send(self, conn, obj, raw=None):
        """给某个客户端发一帧。发失败(对方断了/超限)就顺手清掉。
        raw: 预编码好的字节(用于先查超限再发送的场景), 省得编两次。"""
        fd = conn['sock'].fileno()
        try:
            if raw is None:
                raw = encode(obj)
            conn['sock'].sendall(raw)
        except (OSError, ValueError):
            self.log('WARN', '发送失败, 断开 %s' % (conn['addr'],))
            self._close_fd(fd)

    def _close_fd(self, fd):
        conn = self.conns.pop(fd, None)
        if conn is None:
            return
        role = conn['role']
        if role and self.role_fd.get(role) == fd:
            del self.role_fd[role]
        try:
            self.sel.unregister(conn['sock'])
        except Exception:
            pass
        try:
            conn['sock'].close()
        except OSError:
            pass
        self._sync_online_yx()      # 灭掉/刷新通信状态灯
        self.log('INFO', '连接关闭 %s (role=%s)' % (conn['addr'], role))

    def _nack(self, conn, reason, extra=None):
        obj = {'code': 'NACK', 'ts': now_ms(), 'src': 'GRID_SIM',
               'data': {'reason': reason}}
        if extra:
            obj['data'].update(extra)
        self._send(conn, obj)

    # ---------- selectors 事件 ----------
    def _on_accept(self, lsock):
        sock, addr = lsock.accept()
        sock.setblocking(False)
        fd = sock.fileno()
        self.conns[fd] = {
            'sock': sock, 'addr': addr, 'role': None,
            'buf': b'', 'last_recv': time.time(),
        }
        self.sel.register(sock, selectors.EVENT_READ, self._on_read)
        self.log('INFO', '新连接接入 %s (未注册)' % (addr,))

    def _on_read(self, sock):
        fd = sock.fileno()
        # 容错: selectors 的 events 队列里可能残存 _close_fd 之后才被分发的
        # 陈旧 key —— 此时 sock.fileno() 可能返回 -1(已 close), 或者 fd
        # 已被内核复用给了别的 socket、但 dict 早就 pop 掉过。两种都
        # "查不到 conn", 一律当无事发生, 不许爆炸 —— 2026-09-09 修复。
        if fd == -1:
            return
        conn = self.conns.get(fd)
        if conn is None:
            return
        try:
            chunk = sock.recv(4096)
        except OSError:
            self._close_fd(fd)
            return
        if not chunk:                      # 对方正常关闭
            self._close_fd(fd)
            return

        conn['buf'] += chunk
        # 收到任何一字节都算对方还活着 —— 必须刷新 last_recv。
        # 之前这里漏写了, 导致 last_recv 一直停在"连接建立时刻",
        # 不管对方多活跃, offline 检测都会在 ~15s 后无脑踢人 —— 2026-09-09 修复。
        conn['last_recv'] = time.time()
        # 防异常粘包: 缓冲超上限还没凑到换行 -> 协议错, 清空重来
        if conn['buf'].find(b'\n') == -1 and len(conn['buf']) > MAX_FRAME:
            self.log('ERROR', '%s 单帧超 %d 字节无换行, 丢弃缓冲' % (conn['addr'], MAX_FRAME))
            conn['buf'] = b''
            return

        while True:
            i = conn['buf'].find(b'\n')
            if i == -1:
                break
            line = conn['buf'][:i]
            conn['buf'] = conn['buf'][i + 1:]
            try:
                text = line.decode('utf-8')
            except UnicodeDecodeError:
                self._nack(conn, 'PARSE_ERR')
                self.log('WARN', '%s 收到非 UTF-8 帧' % (conn['addr'],))
                continue
            try:
                self._process_line(conn, text)
            except Exception as e:      # 保险: 单帧异常绝不拖垮整台总机
                self.log('ERROR', '%s 处理帧异常: %r' % (conn['addr'], e))
                self._close_fd(fd)
                return

    # ---------- 一帧一帧处理 ----------
    def _process_line(self, conn, text):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            self._nack(conn, 'PARSE_ERR')
            self.log('WARN', '%s JSON 解析失败: %.60s' % (conn['addr'], text))
            return

        code = obj.get('code')
        src = obj.get('src')
        if not code:
            self._nack(conn, 'MISSING_FIELD', {'field': 'code'})
            return

        role = conn['role']
        # 未注册的连接只允许发 REG, 其余一律断开(见 interface.md §4.6)
        if role is None and code != 'REG':
            self.log('WARN', '%s 未注册就发 %s, 断开' % (conn['addr'], code))
            self._close_fd(conn['sock'].fileno())
            return
        # 已注册连接, src 必须是自己身份(EMS 只能发 EMS 的帧)
        if role is not None and src != role:
            self._nack(conn, 'BAD_SRC', {'expect': role})
            self.log('WARN', '%s 声称 src=%s 但注册身份是 %s' % (conn['addr'], src, role))
            return

        if code == 'REG':
            self._on_reg(conn, obj)
        elif code == 'HEART':
            # 心跳只是报平安, 不需要回(每帧到达都已刷新 last_recv)
            self.log('DEBUG', '%s 心跳' % (conn['addr'],))
        elif code in ('PULL_ALL', 'PULL_DELTA'):
            if role == 'EMS':              # 只有 EMS 能拉数据
                self._on_pull(conn, code, obj)
            else:
                self._nack(conn, 'NOT_READY')
        elif code == 'PUSH':
            # PUSH 是 A->C 的单向下行通道; 谁给 A 发 PUSH 都是方向用错
            self._nack(conn, 'NOT_ALLOWED')
            self.log('WARN', '%s 向 A 发 PUSH, 方向不对' % (conn['addr'],))
        elif code in ('YK', 'YT'):
            self._on_command(conn, code, obj)   # P2-3 指令链
        else:
            self._nack(conn, 'UNKNOWN_CODE', {'code': code})
            self.log('DEBUG', '%s 未知 code=%s' % (conn['addr'], code))

    def _on_pull(self, conn, code, obj):
        """EMS 拉数据: PULL_ALL 回全量, PULL_DELTA(since) 回变位。
        应答可能很大(比如 since 太久远): 超单帧上限就不硬塞,
        回 NACK(PAYLOAD_TOO_BIG) 请对方先 PULL_ALL 重建副本, 绝不崩连接。"""
        data = obj.get('data') or {}
        if code == 'PULL_ALL':
            resp = {'code': 'RESP_ALL', 'ts': now_ms(), 'src': 'GRID_SIM',
                    'data': {'points': self._snapshot_all()}}
        else:
            since = data.get('since')
            if since is None:
                self._nack(conn, 'MISSING_FIELD', {'field': 'since'})
                return
            resp = {'code': 'RESP_DELTA', 'ts': now_ms(), 'src': 'GRID_SIM',
                    'data': {'points': self._delta_since(int(since))}}
        try:
            raw = encode(resp)
        except ValueError:
            self.log('WARN', '%s 拉取应答超过单帧上限, 回 NACK 请其 PULL_ALL'
                     % (conn['addr'],))
            self._nack(conn, 'PAYLOAD_TOO_BIG', {'hint': 'use PULL_ALL'})
            return
        self._send(conn, resp, raw=raw)

    # ---------- P2-3: 指令链 (YK 遥控 / YT 遥调) ----------
    # 总机只当"收单员": 验单合格就写进指令信箱(yk/yt_realtime, state=0),
    # 回 ACK; 不合格回 NACK。真正"动手"是 main.py 每秒扫信箱执行的活。

    def _cmd_nack(self, conn, reason, rtu, typ, pt, **extra):
        data = {'rtu': rtu, 'type': typ, 'pt': pt, 'state': 'REJECT',
                'reason': reason}
        data.update(extra)
        self._nack(conn, reason, data)

    @staticmethod
    def _point_exists(cur, typ, rtu, pt):
        """点表里有没有这个 (rtu, type, pt)? YK 查遥控点表, YT 查遥调点表。"""
        tbl = 'rtu_yk_info' if typ == 'YK' else 'rtu_yt_info'
        row = cur.execute(
            'SELECT 1 FROM %s WHERE rtu_id=? AND point_no=?' % tbl, (rtu, pt)
        ).fetchone()
        return row is not None

    @staticmethod
    def _yt_limits(cur, rtu, pt):
        """每个 YT 点的数值量程(越界就拒收)。上限权威值在 device_params 表,
        WT_PITCH_SET 的 30 是协议点表约定(interface.md §3.2)。"""
        if rtu == 'R01' and pt == 1:            # WT_P_SET: 0 ~ 额定功率
            row = cur.execute(
                "SELECT value FROM device_params "
                "WHERE device='WT' AND key='rated_power'"
            ).fetchone()
            return 0.0, float(row[0]) if row else 100.0
        if rtu == 'R01' and pt == 2:            # WT_PITCH_SET: 0 ~ 30
            return 0.0, 30.0
        if rtu == 'R02' and pt == 1:            # DG_P_SET_CMD: 0 ~ 出力上限
            row = cur.execute(
                "SELECT value FROM device_params "
                "WHERE device='DG' AND key='dg_p_max'"
            ).fetchone()
            return 0.0, float(row[0]) if row else 300.0
        return None                             # 查得到点但没给量程 -> 别乱收

    def _on_command(self, conn, code, obj):
        """收 EMS/风机控制器的 YK/YT 指令: 校验三步, 全过才写库排队。
        写库用 INSERT OR REPLACE: 主键 (rtu,pt) 单行, 新指令顶掉旧指令。"""
        src = obj.get('src')                    # _process_line 已保证 == 注册身份
        data = obj.get('data') or {}
        rtu, typ, pt, val = (data.get('rtu'), data.get('type'),
                             data.get('pt'), data.get('val'))
        for f in ('rtu', 'type', 'pt', 'val'):
            if f not in data or data.get(f) is None:
                self._cmd_nack(conn, 'MISSING_FIELD', rtu, typ, pt, field=f)
                return
        if typ != code:
            self._cmd_nack(conn, 'BAD_TYPE', rtu, typ, pt,
                           expect=code, got=typ)
            return
        try:
            con = self._open()
            try:
                cur = con.cursor()
                # ① 点存在?
                if not self._point_exists(cur, typ, rtu, pt):
                    self._cmd_nack(conn, 'UNKNOWN_POINT', rtu, typ, pt)
                    self.log('WARN', '%s 下发不存在的点 %s %s pt%s'
                             % (conn['addr'], rtu, typ, pt))
                    return
                # ② 值类型/③ 量程
                if typ == 'YK':                 # 开关量: 只认 0 或 1
                    if isinstance(val, bool) or val not in (0, 1):
                        self._cmd_nack(conn, 'OVER_RANGE', rtu, typ, pt,
                                       expect='0 or 1', got=val)
                        return
                    cmd = int(val)
                else:                           # 模拟量: 按点查量程
                    limits = self._yt_limits(cur, rtu, pt)
                    if limits is None:
                        self._cmd_nack(conn, 'UNKNOWN_POINT', rtu, typ, pt)
                        return
                    lo, hi = limits
                    try:
                        fval = float(val)
                    except (TypeError, ValueError):
                        self._cmd_nack(conn, 'BAD_VALUE', rtu, typ, pt, got=val)
                        return
                    if not (lo <= fval <= hi):
                        self._cmd_nack(conn, 'OVER_RANGE', rtu, typ, pt,
                                       range='[%g,%g]' % (lo, hi), got=val)
                        return
                    cmd = fval
                # 全过: 入队等待 main 执行
                tbl = 'yk_realtime' if typ == 'YK' else 'yt_realtime'
                if typ == 'YK':
                    cur.execute(
                        'INSERT OR REPLACE INTO yk_realtime'
                        '(rtu_id, point_no, cmd, src, state) VALUES (?,?,?,?,0)',
                        (rtu, pt, cmd, src),
                    )
                else:
                    cur.execute(
                        'INSERT OR REPLACE INTO yt_realtime'
                        '(rtu_id, point_no, value, src, state) VALUES (?,?,?,?,0)',
                        (rtu, pt, cmd, src),
                    )
                con.commit()
            finally:
                con.close()
        except Exception as e:
            self.log('ERROR', '指令入库失败: %r' % e)
            self._cmd_nack(conn, 'INTERNAL', rtu, typ, pt, msg=str(e))
            return
        self._send(conn, {'code': 'ACK', 'ts': now_ms(), 'src': 'GRID_SIM',
                          'data': {'rtu': rtu, 'type': typ, 'pt': pt,
                                   'state': 'OK', 'val': cmd}})
        # 同一条指令 5s 内重复到达 -> 终端打印 + "(重复, 不写库)"
        sig = (src, rtu, typ, pt, cmd)
        now_s = time.time()
        dup = (sig == self._cmd_sig and now_s - self._cmd_sig_t < 5.0)
        self._cmd_sig, self._cmd_sig_t = sig, now_s
        self.log('INFO', '指令入队: %s -> %s %s pt%s = %s%s'
                 % (src, rtu, typ, pt, cmd, ' (重复,不写库)' if dup else ''),
                 to_db=not dup)

    # ---------- P2-3: 通信状态灯 ----------
    # 三个 YX 点由总机按"谁在线"维护, main.py 只保底建行(0)不覆盖:
    #   R01:3 WIFI_STA / R03:3 COMM_WT = 风机控制器(C) 在线?
    #   R03:2 COMM_EMS                 = EMS(B) 在线?
    def _sync_online_yx(self):
        ems_on = 1 if self.role_fd.get('EMS') is not None else 0
        wt_on  = 1 if self.role_fd.get('WT_CTRL') is not None else 0
        try:
            con = self._open()
            try:
                cur = con.cursor()
                cur.execute('INSERT OR IGNORE INTO yx_realtime'
                            "(rtu_id, point_no, value) VALUES ('R03',2,0)")
                cur.execute('INSERT OR IGNORE INTO yx_realtime'
                            "(rtu_id, point_no, value) VALUES ('R03',3,0)")
                cur.execute('INSERT OR IGNORE INTO yx_realtime'
                            "(rtu_id, point_no, value) VALUES ('R01',3,0)")
                cur.execute("UPDATE yx_realtime SET value=? "
                            "WHERE rtu_id='R03' AND point_no=2", (ems_on,))
                cur.execute("UPDATE yx_realtime SET value=? "
                            "WHERE rtu_id='R03' AND point_no=3", (wt_on,))
                cur.execute("UPDATE yx_realtime SET value=? "
                            "WHERE rtu_id='R01' AND point_no=3", (wt_on,))
                con.commit()
            finally:
                con.close()
        except Exception as e:
            self.log('WARN', '通信状态灯同步失败: %s' % e)

    def _on_reg(self, conn, obj):
        fd = conn['sock'].fileno()
        data = obj.get('data') or {}
        role = data.get('role')
        if role not in VALID_ROLES:
            self._nack(conn, 'BAD_SRC')
            self.log('WARN', '%s 注册了非法 role=%r' % (conn['addr'], role))
            return
        # 同身份只留一个: 新的踢旧的
        old_fd = self.role_fd.get(role)
        if old_fd is not None and old_fd != fd:
            self.log('WARN', '%s 重复注册 role=%s, 踢掉旧连接' % (conn['addr'], role))
            self._close_fd(old_fd)
        conn['role'] = role
        self.role_fd[role] = fd
        self._send(conn, {'code': 'ACK', 'ts': now_ms(), 'src': 'GRID_SIM',
                          'data': {'state': 'OK', 'role': role}})
        self._sync_online_yx()      # 点亮/刷新通信状态灯
        self.log('INFO', '注册成功: %s -> role=%s' % (conn['addr'], role))

    # ---------- 周期任务: 心跳广播 + 推送给C + 离线清理 ----------
    def _periodic(self):
        t = time.time()
        # 每 5s 给所有已注册连接广播一次心跳(带 sim_time, 让 B/C 对时)
        if t - self._last_heart >= self.heart_period:
            self._last_heart = t
            sim_time = self._read_sim_time()
            frame = {'code': 'HEART', 'ts': now_ms(), 'src': 'GRID_SIM',
                     'data': {'sim_time': sim_time}}
            for conn in list(self.conns.values()):
                if conn['role']:
                    self._send(conn, frame)
        # 每 1s 给风机控制器推一次 PUSH(风速+功率设定), 顺带兜底刷新通信状态灯
        if t - self._last_push >= self.push_period:
            self._last_push = t
            self._push_to_wt()
            self._sync_online_yx()
        # 离线判定: 超过超时没任何报文 -> 断
        for fd, conn in list(self.conns.items()):
            if conn['role'] and t - conn['last_recv'] > self.offline_timeout:
                self.log('WARN', '%s (%s) 超时无报文, 踢除'
                         % (conn['addr'], conn['role']))
                self._close_fd(fd)

    # ---------- 主循环 ----------
    def run_forever(self):
        self.log('INFO', '总机已启动 0.0.0.0:%s, 等 B/C 拨入...' % self.lsock.getsockname()[1])
        try:
            while self._running:
                events = self.sel.select(timeout=1.0)
                for key, _mask in events:
                    if key.fileobj is self.lsock:
                        self._on_accept(self.lsock)
                    else:
                        self._on_read(key.fileobj)
                self._periodic()
        except KeyboardInterrupt:
            self.log('INFO', '收到 Ctrl+C, 总机关闭')
        finally:
            for fd in list(self.conns):
                self._close_fd(fd)
            self.sel.close()
            self.lsock.close()


def main():
    ap = argparse.ArgumentParser(description='A(电网模拟器) TCP 总机')
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=9000)
    args = ap.parse_args()
    GridSimServer(args.host, args.port).run_forever()


if __name__ == '__main__':
    main()
