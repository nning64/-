"""
main.py - 电网模拟器 (A) 主程序 - P2-3 整合版(仿真 + 通讯总机同一进程)

目标: 每秒推一格时钟: 读环境曲线 -> 执行新指令 -> 风机/柴发模型 ->
        写 sim_history, 同时把通讯总机(comm/server.py)跑在后台线程里。
        风机模型: core/wind_model.py (WindTurbine, P1-1 完成);
        柴发模型: core/dg_model.py   (DieselGen,  P1-2 完成)。

节奏语义(2026-09-08): 每秒固定推进一帧, 帧内等 1.0/speed 秒;
每帧推进 sim_step_s 个仿真秒。故 实际倍速 = speed × sim_step_s。
(step 默认 1 时与"每秒一格=1x 实时"一致; step 调大即快进)。

P2-3 起一条命令全包(仿真 + 总机 9000 一起):
    PyCharm 右键本文件 Run  (推荐, 默认开通讯总机, EMS/风机控制器可拨入)
    或命令行:  python main.py
              python main.py --no-comm          # 退回纯仿真, 不开总机
              python main.py --port 9001        # 换端口
              python main.py --scenario scenarios/02_适宜风速_风机满发.csv
              python main.py --speed 10         # 10 倍速
              python main.py --until 120        # 跑到 t=120s 自动停

按 Ctrl+C 结束, 仿真时间自然停止, db 已自动落盘。
"""

import argparse
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB   = ROOT / 'grid.db'

sys.path.insert(0, str(ROOT))          # 让 import core.* / comm.* 生效
from core.wind_model import WindTurbine
from core.dg_model   import DieselGen
from comm.server     import GridSimServer   # P2-3: 通讯总机(后台线程)
from core.syslog     import log as db_log   # 关键事件入 log 表(UI 运行日志)

# ---------------- 工具函数 ----------------

def get_param(cur, key, default=None):
    row = cur.execute(
        'SELECT value FROM sim_params WHERE key=?', (key,)
    ).fetchone()
    return row[0] if row else default

def set_param(cur, key, value):
    cur.execute(
        'UPDATE sim_params SET value=?, updated_at=datetime(\'now\',\'localtime\') '
        'WHERE key=?', (str(value), key),
    )

def env_curve_lookup(cur, t, period=None):
    """在 env_curve 里找 t 时刻的风速/负荷。
    period > 0 时按 period 取模,让场景跑超后从起点循环(一日典型工况演示用)。"""
    if period and period > 0:
        t = t % period
    row = cur.execute(
        'SELECT wind_speed, load_kw FROM env_curve '
        'WHERE t <= ? ORDER BY t DESC LIMIT 1', (t,)
    ).fetchone()
    if row is None:
        return 0.0, 0.0
    return row[0], row[1]

def wt_params(cur):
    rows = cur.execute(
        "SELECT key, value FROM device_params WHERE device='WT'"
    ).fetchall()
    return {k: float(v) if v is not None else 0.0 for k, v in rows}

def dg_params(cur):
    """柴发参数从 device_params 表读, 不许在代码里硬编码(曾硬编码 10/200 与表不符)。"""
    rows = cur.execute(
        "SELECT key, value FROM device_params WHERE device='DG'"
    ).fetchall()
    return {k: float(v) if v is not None else 0.0 for k, v in rows}

# ---------------- 当前值表刷新 (P1-3) ----------------
# yc_realtime / yx_realtime 是"公告栏": 每台设备最新状态只留一份,
# 主键 (rtu_id, point_no), 每秒覆盖写。将来 B(EMS) 发 PULL 拉数据,
# 就是读这两张表 —— 这是 A 交给 B 的前置取数口。

def write_realtime(cur, wind, load_kw, wt_act, wt_pitch, wt_run, wt_avail,
                   wt_ack, dg_act, dg_run, unbalance, dg_p,
                   dg_ack=0.0, sim_state=1):
    """把这一秒的全网状态, 按点表逐点写进 yc_realtime(11行) / yx_realtime(8行)。
    wt_avail: 可用功率(A 按风功率曲线自算, MPPT 上限)
    wt_ack  : R01:5 功率设定回显值(见主循环处说明)
    dg_ack  : R02:2 功率设定回显值 —— EMS 下发过用指令值; 没下用系统目标 dg_sp;
              永远有值, 让 UI 不会因重启/B 未连而闪烁消失。
    """
    # 遥测 YC: (rtu_id, point_no, value, quality)  0=好值 1=坏值
    yc_rows = [
        # R01 风机
        ('R01', 1, round(wind,      2), 0),   # 风速 W_SPD
        ('R01', 2, round(wt_act,    2), 0),   # 风机有功 WT_ACT
        ('R01', 3, round(wt_pitch,  2), 0),   # 桨距角 WT_PITCH
        ('R01', 4, round(wt_avail,  2), 0),   # 可用功率 WT_AVAIL: A 按风速+功率曲线自算(MPPT 上限),
                                              # 不受限功率指令影响 —— 限功率时 4(可用)与 2(实发)才分开
        ('R01', 5, round(wt_ack,    2), 0),   # 功率设定回显: EMS 下发 YT1 限功率 -> 回显设定值;
                                              # 没下过 -> 回显可用功率(就地满发上限)
        # R02 柴发
        ('R02', 1, round(dg_act,    2), 0),   # 柴发出力 DG_ACT
        ('R02', 2, round(dg_ack,    2), 0),   # 功率设定回显: EMS 下发 -> 指令值;
                                              # 没下发 -> 系统目标 dg_sp(EMS 没指令时 A 就地补缺的目标)
        ('R02', 3, round(dg_act / dg_p.get('dg_rated', 300.0) * 100, 2), 0),  # 负荷率 DG_LOAD %
        # R03 电网/负荷
        ('R03', 1, round(load_kw,   2), 0),   # 负荷用电 LD_ACT
        ('R03', 2, round(unbalance, 2), 0),   # 不平衡功率 UNBAL (负荷-风机-柴发)
        ('R03', 3, 50.00, 0),                 # 频率 GRID_FREQ: 预留占位, 频率模型后续再做
    ]
    # 遥信 YX: (rtu_id, point_no, value)  0/1
    # 注意: 三个"通信状态"点(R01:3 WIFI_STA / R03:2 COMM_EMS / R03:3 COMM_WT)
    # 归通讯总机(comm/server.py)按 EMS/风机是否在线维护, 本函数只保底建行(0)
    # 并读回现值用于变位记账 —— 绝不覆盖总机写的在线状态(见函数尾部)
    yx_rows = [
        ('R01', 1, wt_run),   # 风机运行状态 (0停/1运)
        ('R01', 2, 0),        # 风机故障标志 (0正常)
        ('R02', 1, dg_run),   # 柴发运行状态
        ('R02', 2, 0),        # 柴发告警   (0正常)
        ('R03', 1, sim_state), # 仿真运行状态 (0停/1运/2暂停) — 跟随 sim_state, 不再硬编码 1
    ]
    # updated_at 走表默认值(真实本地时间), 覆盖写后自动刷新
    cur.executemany(
        'INSERT OR REPLACE INTO yc_realtime(rtu_id, point_no, value, quality) '
        'VALUES (?,?,?,?)', yc_rows,
    )
    cur.executemany(
        'INSERT OR REPLACE INTO yx_realtime(rtu_id, point_no, value) '
        'VALUES (?,?,?)', yx_rows,
    )
    # 通信三灯: 只建默认 0 的行(若还没有), 再读回当前值 —— 在线状态由总机写
    for rtu, pt in (('R01', 3), ('R03', 2), ('R03', 3)):
        cur.execute('INSERT OR IGNORE INTO yx_realtime(rtu_id, point_no, value) '
                    'VALUES (?,?,0)', (rtu, pt))
        row = cur.execute('SELECT value FROM yx_realtime '
                          'WHERE rtu_id=? AND point_no=?', (rtu, pt)).fetchone()
        yx_rows.append((rtu, pt, int(row[0])))
    # 把这一秒的点列表原样交给调用方(P1-4 变位记录器要复用同一份数据)
    return yc_rows, yx_rows

# ---------------- 变位历史记录 (P1-4) ----------------
# yc_history / yx_history 是"变动账本": 不像 sim_history 每秒全量记,
# 而是按点表里每个点写的死区(deadband)过滤 —— 这一秒的值比上次记的
# 差得够多(越过死区)才追加一条。遥信(YX)是 0/1 开关量, 没有死区,
# 规矩是"状态一变就记"。这样账本不会每秒膨胀, 又能还原完整变化曲线。

class HistoryRecorder:
    """一台"记得住每个点上次记了啥"的记账员。"""

    def __init__(self, cur):
        # ① 从点表抄死区: {(rtu, pt): 死区}
        self.deadband = {}
        for rtu, pt, db in cur.execute(
            'SELECT rtu_id, point_no, deadband FROM rtu_yc_info'
        ):
            self.deadband[(rtu, pt)] = db if db is not None else 0.0
        # ② 从账本恢复"上次记的值": 换场景时账本被清空 -> 恢复为空 ->
        #    下次运行每个点第一秒都会先落一条(当基线); 中途重启则接着记
        self.last_yc = {}
        self.last_yx = {}
        for rtu, pt, v in cur.execute(
            'SELECT rtu_id, point_no, value FROM yc_history h '
            'WHERE sim_time = (SELECT max(sim_time) FROM yc_history h2 '
            '                   WHERE h2.rtu_id = h.rtu_id '
            '                     AND h2.point_no = h.point_no)'
        ):
            self.last_yc[(rtu, pt)] = v
        for rtu, pt, v in cur.execute(
            'SELECT rtu_id, point_no, value FROM yx_history h '
            'WHERE sim_time = (SELECT max(sim_time) FROM yx_history h2 '
            '                   WHERE h2.rtu_id = h.rtu_id '
            '                     AND h2.point_no = h.point_no)'
        ):
            self.last_yx[(rtu, pt)] = v

    @staticmethod
    def _crossed(db, old, new):
        """判断 new 相对 old 是否"越过死区"。"""
        if old is None:                      # 头一回见这个点 -> 落基线
            return True
        if db and db > 0:                    # 有正常死区 -> 差得够多才记
            return abs(new - old) >= db
        return new != old                    # 死区为 0 -> 值一变就记

    def yc_step(self, cur, rows, sim_time):
        """把这一秒的遥测点列表过一遍死区, 越过的追加进 yc_history。"""
        for rtu, pt, value, _q in rows:
            key = (rtu, pt)
            if self._crossed(self.deadband.get(key, 0.0),
                             self.last_yc.get(key), value):
                cur.execute(
                    'INSERT INTO yc_history(rtu_id, point_no, value, sim_time) '
                    'VALUES (?,?,?,?)', (rtu, pt, round(value, 2), sim_time),
                )
                self.last_yc[key] = value

    def yx_step(self, cur, rows, sim_time):
        """把这一秒的遥信点列表过一遍, 状态变了才追加进 yx_history。"""
        for rtu, pt, value in rows:
            key = (rtu, pt)
            if self.last_yx.get(key) != value:
                cur.execute(
                    'INSERT INTO yx_history(rtu_id, point_no, value, sim_time) '
                    'VALUES (?,?,?,?)', (rtu, pt, value, sim_time),
                )
                self.last_yx[key] = value

# ---------------- 指令执行 (P2-3) ----------------
# 闭环的一环: EMS/风机控制器下发的 YK(遥控)/YT(遥调) 先进 yk_realtime/
# yt_realtime 排"待执行"(state=0)。本执行员每秒扫一次信箱, 把新指令变成
# 对模型的约束(存内存), 盖"已执行"章并记入历史。
# 每个 (rtu, pt) 只留最新一条指令(主键单行): 还没执行就被新指令顶掉时,
# 旧指令按"丢弃"处理(不算执行, 不记历史)。

# C 桨距"在线"判定窗口(真实秒): C 协议约定每 1s 发一次 YT2(WT_PITCH_SET),
# 断发超过该窗口即视为 C 掉线/停止控制, main 把桨距控制权收回 A 自演,
# 重新按 EMS 限功率钳位 —— 防止 C 断了之后风机冻在旧桨距上随风乱跑。
C_PITCH_HOLD_S = 5.0

class CommandExecutive:
    """一台"听指令"的执行员。整场仿真构造一次, 记住当前生效的每条设定。"""

    # 指令点 -> 模型控制量 的对照表 (不在表里的点只收单归档, 不生效)
    YK_MAP = {('R01', 1): 'wt_start', ('R02', 1): 'dg_run'}   # 遥控 -> 启停
    YT_MAP = {('R01', 1): 'wt_p_set',  ('R01', 2): 'wt_pitch_set',
              ('R02', 1): 'dg_set'}    # 遥调 -> 功率/桨距设定

    def __init__(self):
        # 当前生效的设定。None = 还没收到过该指令 -> 用就地策略:
        #   wt_start: 风机启停 0/1  (None = 风速自动决定)
        #   dg_run  : 柴发启停 0/1  (None = 就地允许运行)
        #   wt_p_set: 风机限功率 kW (None = 不限, 按风速满发 MPPT)
        #   wt_pitch_set: C 桨距设定 deg (None = C 未下发过 -> A 自演)
        #   wt_pitch_ts : 最近一次执行 C YT2 的墙钟时刻(None=从未), 判 C 在线用
        #   dg_set  : EMS 下发的柴发遥调目标 kW (仅供收单/展示;
        #             自 2026-09-11 起不作为出力, 出力由 A 按 负荷-风机 就地
        #             计算, 决策权归 A) —— YT_MAP 里仍保留以便指令进历史
        self.s = {'wt_start': None, 'dg_run': None,
                  'wt_p_set': None, 'wt_pitch_set': None,
                  'wt_pitch_ts': None, 'dg_set': None}

    def poll(self, cur, sim_time):
        """扫一遍指令信箱(state=0 的待执行), 逐条执行并归档。
        返回 [(src, type, rtu, pt, val)], 供主循环打印; 空列表=没新指令。"""
        done = []
        # 遥控 YK: 目标值 0/1 (启停)
        for rtu, pt, cmd, src in cur.execute(
            'SELECT rtu_id, point_no, cmd, src FROM yk_realtime WHERE state=0'
        ):
            c = cur.execute(
                "UPDATE yk_realtime SET state=1, "
                "executed_at=datetime('now','localtime') "
                "WHERE rtu_id=? AND point_no=? AND state=0", (rtu, pt))
            if c.rowcount == 0:        # 已被更新的指令顶掉 -> 旧指令丢弃
                continue
            key = (rtu, pt)
            if key in self.YK_MAP:
                self.s[self.YK_MAP[key]] = cmd
            cur.execute(
                'INSERT INTO yk_history(rtu_id, point_no, cmd, src, sim_time) '
                'VALUES (?,?,?,?,?)', (rtu, pt, cmd, src, sim_time))
            done.append((src, 'YK', rtu, pt, cmd))
        # 遥调 YT: 目标值 模拟量 (功率/桨距设定)
        for rtu, pt, val, src in cur.execute(
            'SELECT rtu_id, point_no, value, src FROM yt_realtime WHERE state=0'
        ):
            c = cur.execute(
                "UPDATE yt_realtime SET state=1, "
                "executed_at=datetime('now','localtime') "
                "WHERE rtu_id=? AND point_no=? AND state=0", (rtu, pt))
            if c.rowcount == 0:
                continue
            key = (rtu, pt)
            if key in self.YT_MAP:
                self.s[self.YT_MAP[key]] = val
            if key == ('R01', 2):        # C 的桨距设定 YT2: 记"最近一次到达"的
                self.s['wt_pitch_ts'] = time.time()   # 墙钟时刻, 主循环据此判 C 是否在线
            cur.execute(
                'INSERT INTO yt_history(rtu_id, point_no, value, src, sim_time) '
                'VALUES (?,?,?,?,?)', (rtu, pt, val, src, sim_time))
            done.append((src, 'YT', rtu, pt, val))
        return done

# ---------------- 仿真控制面 (P3) ----------------
# UI(另一进程)通过写 sim_params 的 ctrl_cmd 来遥控仿真:
#   start  -> 把 sim_time 重置为 sim_start_s, 置 sim_state=1
#   pause  -> 置 sim_state=2 (冻结, 数据保持)
#   resume -> 置 sim_state=1 (接着跑)
#   stop   -> 置 sim_state=0 (本轮结束, 主循环 break; 进程仍活着便于下次 start)
# 主循环每帧读一次 ctrl_cmd 并消费(清空), 冻结时也要读 —— 否则停不下来。

def _apply_ctrl(cur, period):
    """消费 ctrl_cmd。返回 'stop' 表示应结束本轮, 其他情况返回 None。"""
    cmd = get_param(cur, 'ctrl_cmd', '')
    if not cmd:
        return None
    set_param(cur, 'ctrl_cmd', '')          # 消费掉, 防止重复执行
    if cmd == 'start':
        start = int(get_param(cur, 'sim_start_s', '0') or 0)
        # 起始时刻原样使用: env_curve_lookup 内部按 period 取模, 即使
        # start >= period 也能正确取场景值(loop 模式), 无需在此折回。
        # 清历史(与 UI 协调: UI 已清一次, 此处再清一次防漏, 用户
        # 视觉上 ms 级无感)。UI 清空 + main 清空, 任何顺序都安全。
        for tbl in ('sim_history', 'yc_history', 'yx_history'):
            cur.execute(f'DELETE FROM {tbl}')
        set_param(cur, 'sim_time', start)
        set_param(cur, 'sim_state', '1')
        db_log('INFO', 'MAIN', '仿真启动 (从 t=%ss, 历史已清)' % start)
        print(f'  [+CTRL] 启动 -> 从 t={start}s 开始 (历史已清)')
    elif cmd == 'pause':
        set_param(cur, 'sim_state', '2')
        db_log('INFO', 'MAIN', '仿真暂停')
        print('  [+CTRL] 暂停')
    elif cmd == 'resume':
        set_param(cur, 'sim_state', '1')
        db_log('INFO', 'MAIN', '仿真继续')
        print('  [+CTRL] 继续')
    elif cmd == 'stop':
        set_param(cur, 'sim_state', '0')
        db_log('INFO', 'MAIN', '仿真停止')
        print('  [+CTRL] 停止 -> 本轮结束')
        return 'stop'
    return None


def reload_params(cur, wt_tb, dg_tb):
    """每帧从 device_params 读参数并热更新到模型对象(UI 改表立刻生效)。
    同时返回最新的 dg_p 字典, 让调用方用于 DG_LOAD(%) 等每帧计算 —— 之前
    dg_p 在 main 启动时读一次快照, UI 改 dg_rated 后 DG_LOAD 不刷新。"""
    wt_tb.update_params(wt_params(cur))
    dg_p = dg_params(cur)
    dg_tb.update_params(dg_p)
    return dg_p


# ---------------- 仿真主循环 ----------------

def run_loop(scenario=None, speed=1.0, until=None, comm=False, port=9000,
             loop=True):
    """跑仿真主循环。comm=True 时额外把通讯总机(comm/server.py)起在后台线程。
    comm 默认关: 命令行入口默认开(--comm), 别的脚本 import 调 run_loop 不受干扰。
    loop=True 时场景跑超后按 env_curve 的总时长循环(一日典型工况演示用);
    loop=False 时钳在最后一帧(老行为, 用于单工况精细观察)。"""
    if not DB.exists():
        print(f'[X] 找不到 {DB}, 先 python tools/build_db.py 建库')
        sys.exit(1)

    # 1) 如果指定了场景, 先加载
    if scenario:
        sys.path.insert(0, str(ROOT / 'tools'))
        from load_scenario import load
        load(Path(scenario) if Path(scenario).is_absolute() else ROOT / scenario)

    conn = sqlite3.connect(str(DB), isolation_level=None)  # autocommit
    cur  = conn.cursor()

    # 2) 检查场景是否已加载
    n = cur.execute('SELECT count(*) FROM env_curve').fetchone()[0]
    if n == 0:
        print('[X] env_curve 是空的, 请先 python tools/load_scenario.py')
        sys.exit(1)

    # 2.5) 通讯总机(可选): 与仿真共用同一个 db, 起在后台线程
    server = None
    if comm:
        try:
            server = GridSimServer(host='0.0.0.0', port=port)
            threading.Thread(target=server.run_forever, daemon=True,
                             name='comm-server').start()
        except OSError as e:
            print(f'[X] 通讯总机启动失败: {e}')
            print('    多半是端口被占用(还有旧的 server/main 进程在跑?)')
            print('    关掉占用端口的进程重试, 或加 --no-comm 只跑仿真。')
            sys.exit(1)

    set_param(cur, 'sim_state', '1')
    set_param(cur, 'sim_start_real', time.strftime('%Y-%m-%d %H:%M:%S'))
    # 确保"仿真控制"相关 key 存在(UI 会读写这些; 老库可能还没有)
    for k, v in (('sim_start_s', '0'), ('sim_end_s', '0'), ('ctrl_cmd', '')):
        cur.execute('INSERT OR IGNORE INTO sim_params(key, value) VALUES (?,?)',
                    (k, v))

    # 取场景周期: env_curve 最大 t + 1 (= 最后一秒 + 1) 作为模长
    # 循环模式下,sim_time % period 会在 0~period-1 间来回,曲线自然重复
    curve_max_t = cur.execute('SELECT MAX(t) FROM env_curve').fetchone()[0] or 0
    period = (curve_max_t + 1) if loop else None

    wt_p = wt_params(cur)
    dg_p = dg_params(cur)
    wt_tb = WindTurbine(wt_p)   # 一台"有记忆"的风机(记得上一秒桨距), 整场仿真只造一次
    dg_tb = DieselGen(dg_p)     # 一台"有身体"的柴发(记得上一秒出力), 同上
    hrec  = HistoryRecorder(cur)  # 一位"记得住每个点上次记了啥"的记账员, 同上
    cexe  = CommandExecutive()    # 一位"听 EMS 指令"的执行员, 同上 (P2-3)
    sim_time = int(get_param(cur, 'sim_time', '0'))

    # ---- 节奏基准(2026-09-11 修"流速不均匀"):
    #     旧实现"先 sleep(1.0/speed) 再 work"会让工作耗时叠加在节奏上,
    #     speed 越大偏差越大 (10x 时 25ms 工作 = 25% 抖动) 且 work 抖动
    #     直接变成倍速漂移。改为"目标时间驱动":
    #     记录 next_deadline = 下一帧应该跑的真实时刻; 每帧等到它(已晚
    #     则不补睡) -> work -> sim_time += step -> deadline += frame_dt。
    #     work 多久都不影响节奏, 整体累计偏差趋近 0; pause / 重新 start
    #     / 改 speed 都要以"现在"重置 deadline, 否则会试图"追回"暂停时间。
    speed_base = max(speed, 1e-9)
    frame_dt   = 1.0 / speed_base       # 一帧 = 1.0/speed 真实秒
    next_deadline = time.perf_counter() + frame_dt
    prev_running  = True                # 上一帧是否处于运行态 (1)
    started  = time.time()
    db_log('INFO', 'MAIN',
           '仿真启动 sim=%ss speed=%sx until=%s' % (sim_time, speed, until))

    print()
    print('=' * 60)
    print(f'仿真启动  sim_time={sim_time}s  speed={speed}x  until={until}')
    print(f'风机模型: core/wind_model.py (WindTurbine)')
    print(f'柴发模型: core/dg_model.py   (DieselGen, 爬坡率={dg_tb.ramp}kW/s)')
    print(f'风机参数: {wt_p}')
    print(f'柴发参数: {dg_p}')
    print(f'场景范围: env_curve {n} 条  (max_t={curve_max_t}s)')
    if period:
        print(f'场景循环: ON (每 {period} 秒重复一次, 过末尾自动折回起点)')
    else:
        print(f'场景循环: OFF (过 max_t={curve_max_t}s 后钳在末帧)')
    print('实时刷新: yc_realtime(11点)/yx_realtime(8点) 每秒覆盖 -> B拉数口')
    print('变位记账: yc_history/yx_history 越过死区才落一条 (P1-4)')
    print('指令链  : EMS/风机 YK/YT -> 信箱排队 -> 每秒执行 -> 盖已执行章 (P2-3)')
    if comm:
        print(f'通讯总机: 0.0.0.0:{port} 已启动 (后台线程, EMS/风机控制器可拨入)')
    else:
        print('通讯总机: 未启动 (命令行加 --comm / 去掉 --no-comm 可打开)')
    print('按 Ctrl+C 停止')
    print('=' * 60)

    last_print_t = -1
    c_ctl_prev = False      # 上一帧 C 是否接管桨距(只在切换时打一行日志)
    last_yx_state = None    # 已写进 R03:1 的仿真状态(只在变化时才写, 见下)
    last_cmd_log = {}       # (src,typ,rtu,pt,val) -> 上次入 log 表的时刻(去重)
    try:
        while True:
            # 0. 消费"仿真控制"命令(UI 下发的 ctrl_cmd) —— 冻结时也得听
            if _apply_ctrl(cur, period):
                break
            # sim_time 以 DB 为准: 每帧开头重读一次, 使 start(把 DB 里的
            # sim_time 重置成 sim_start_s)真正生效 —— 否则局部变量会把旧值写回去
            sim_time = int(get_param(cur, 'sim_time', '0') or 0)
            cur_step  = int(get_param(cur, 'sim_step_s', '1') or 1)
            cur_state = int(get_param(cur, 'sim_state', '1') or 1)

            # 0.5 状态灯 R03:1 (SIM_RUN) 无条件同步 —— 运行/暂停/停止三种状态
            #     都要落进 yx_realtime。旧实现只在"运行帧"的 write_realtime 里
            #     写它, 一旦按了暂停/停止, 这个点就再也不更新, UI 顶部/左栏
            #     一直亮着绿灯"仿真运行", 而 t 和曲线全冻住 —— 用户会以为
            #     "界面卡死、数据不刷新"。只在实际变化时写, 不空转写库。
            #     (2026-09-11 修)
            if cur_state != last_yx_state:
                cur.execute(
                    "UPDATE yx_realtime SET value=?, "
                    "updated_at=datetime('now','localtime') "
                    "WHERE rtu_id='R03' AND point_no=1", (cur_state,))
                last_yx_state = cur_state

            if cur_state != 1:
                # 暂停(2)/停止(0): 冻结不推进, 只轮询控制命令
                time.sleep(0.2)
                prev_running = False
                continue

            # 1.0 状态/速度变化时重锚: next_deadline 以"现在"为新基准,
            #     否则会试图"追回"暂停期间没推进的时间, 或把 speed 改小后
            #     还按旧 frame_dt 跳。变量在循环外初始化; 此处每帧检测一次。
            cur_running = (cur_state == 1 and speed > 0)
            speed_now   = float(speed)
            if cur_running and (not prev_running
                                or abs(speed_now - speed_base) > 1e-9):
                next_deadline = time.perf_counter() + frame_dt
                speed_base = speed_now
            prev_running = cur_running

            # 1. 目标时间驱动: 等到 next_deadline(已晚则不补睡)
            #    分片睡 <= 0.1s, 让 pause/start 命令能秒级打断。
            if speed > 0:
                while True:
                    now = time.perf_counter()
                    wait_s = next_deadline - now
                    if wait_s <= 0:
                        break                  # 到点了, 不补睡
                    if get_param(cur, 'ctrl_cmd', ''):
                        break                  # 中途来命令, 立即醒
                    time.sleep(min(0.1, wait_s))
                if wait_s <= 0 and not get_param(cur, 'ctrl_cmd', ''):
                    next_deadline += frame_dt  # 正常推进到下一帧

            # 1.5 设备参数热重载(UI 改了 device_params, 下一帧就生效)
            #    同时拿回最新 dg_p 字典给 DG_LOAD 等每帧计算用
            dg_p = reload_params(cur, wt_tb, dg_tb)

            sim_time += cur_step
            set_param(cur, 'sim_time', sim_time)

            # 2. 读环境曲线
            wind, load_kw = env_curve_lookup(cur, sim_time, period)

            # 2.5. 先执行新到的指令(EMS/风机下发的 YK/YT), 变成对模型的约束
            #      注意: EMS/C 常常"每秒重发同一个设定值"(如 R02 pt1=30.0),
            #      若每次都写 log 表, 几分钟就能灌进几万条相同记录把库撑大。
            #      终端 print 保留(方便看实时活动), 但 log 表对同一条命令
            #      5 秒内只记一次。(2026-09-11 修)
            for src, typ, rtu, pt, val in cexe.poll(cur, sim_time):
                print(f'  [CMD] {src} {typ} {rtu} pt{pt} = {val} -> 已执行')
                sig = (src, typ, rtu, pt, val)
                now_real = time.time()
                if now_real - last_cmd_log.get(sig, 0.0) >= 5.0:
                    last_cmd_log[sig] = now_real
                    db_log('INFO', 'MAIN',
                           '执行 %s %s pt%s = %s (src=%s)'
                           % (rtu, typ, pt, val, src))

            # 2.75 桨距控制权判定(2026-09-09 开放 C 真实参与):
            #    C 最近 C_PITCH_HOLD_S 秒(真实时间)内持续发过 YT2 ->
            #    桨距听 C 的(模型不再电子钳位 EMS 限功率, 由 C 靠桨距实现);
            #    断发 / 从未发过 -> 退回 A 自演(重新钳位 p_set), 保限功率不破。
            wt_pitch_ctl = None
            if (cexe.s['wt_pitch_set'] is not None
                    and cexe.s['wt_pitch_ts'] is not None
                    and time.time() - cexe.s['wt_pitch_ts'] <= C_PITCH_HOLD_S):
                wt_pitch_ctl = cexe.s['wt_pitch_set']
            c_ctl = wt_pitch_ctl is not None
            if c_ctl != c_ctl_prev:
                c_ctl_prev = c_ctl
                print('  [CTRL] 风机桨距 %s'
                      % ('C 闭环接管 (听 YT2, A 不再钳位限功率)'
                         if c_ctl else '退回 A 自演 (C 未在线, 恢复 EMS 限功率钳位)'))

            # 3. 风机真实模型: 出力 = 风速能吃多少 x 桨距折减。
            #    p_set(EMS 限功率) / start_cmd(启停) / pitch_set(C 桨距, None=自演)
            #    都是 None = 就地自动(满发)。
            wt_r = wt_tb.step(wind, p_set=cexe.s['wt_p_set'],
                              start_cmd=cexe.s['wt_start'],
                              pitch_set=wt_pitch_ctl)
            wt_act, wt_pitch, wt_run = wt_r['p_act'], wt_r['pitch'], wt_r['run']
            wt_avail = wt_r['p_avail']     # 方案B: 可用功率 A 自算(风功率曲线 MPPT 上限)
            # R01:5 功率设定回显: EMS 下过 YT1 限功率 -> 回显设定值;
            # 没下过(就地 MPPT) -> 回显可用功率, 表示"当前允许发这么多"
            wt_ack = (cexe.s['wt_p_set'] if cexe.s['wt_p_set'] is not None
                      else wt_avail)

            # 4. 柴发决策权收归 A(2026-09-11 改):
            #    EMS 下发的柴发遥调指令(dg_set)只"收单 + 进 yt_history 归档",
            #    不再直接当作当前功率(四遥链路保留, UI 仍可见 EMS 下过什么)。
            #    实际出力目标由 A 按实时功率就地计算 = 负荷 - 风机出力,
            #    使每一刻 不平衡功率 = 负荷 - 风机 - 柴发 趋于 0
            #    (受柴发 p_min/p_max 物理限制时才有残余不平衡: 如风>负荷时
            #     柴发被钳在 p_min, 多余风功率需靠弃风/储能消纳, 本模型不含)。
            #    风机设定功率(wt_p_set)维持 EMS 指令不变 —— 见步骤 3。
            dg_sp = load_kw - wt_act
            dg_run_cmd = cexe.s['dg_run'] if cexe.s['dg_run'] is not None else 1
            dg_r = dg_tb.step(dg_sp, run_cmd=dg_run_cmd)
            dg_act, dg_run = dg_r['p_act'], dg_r['run']

            # 5. 不平衡功率
            unbalance = load_kw - wt_act - dg_act

            # 6. 写 sim_history (全景日记, 每秒一行)
            cur.execute(
                'INSERT OR REPLACE INTO sim_history('
                '  sim_time, wind_speed, load_kw, wt_act_kw, dg_act_kw,'
                '  wt_pitch_deg, wt_run, dg_run, unbalance_kw'
                ') VALUES (?,?,?,?,?,?,?,?,?)',
                (sim_time, wind, load_kw, wt_act, dg_act,
                 wt_pitch, wt_run, dg_run, unbalance),
            )

            # 7. 刷新当前值表 (公告栏, 给 B 的 EMS 拉数据用)
            #    R02:2 柴发功率回显: EMS 下发过 YT(DG_P_SET_CMD) -> 回显指令值;
            #    没下发 -> 回显系统目标 dg_sp(EMS 没指令时 A 就地补缺的目标),
            #    但 dg_sp 可能是负的(风机出力 > 负荷), 此时不能回显负数 —— 柴发
            #    不能关(必须保 p_min=30 最低稳定燃烧), 所以钳到 dg_tb.p_min。
            #    这样 R02:2 永远在 [p_min, p_max] 内, 配合 R02:1(实发)便于看出
            #    "目标 vs 实发"偏差。 (2026-09-10 修)
            # R02:2 功率设定回显: 现在永远回显 A 的实际调度目标(负荷-风机),
            # 不再回显 EMS 指令; 负值(风>负荷)钳到 p_min 以免显示负数。
            dg_ack = max(dg_tb.p_min, dg_sp)
            yc_rows, yx_rows = write_realtime(
                cur, wind, load_kw, wt_act, wt_pitch, wt_run, wt_avail,
                wt_ack, dg_act, dg_run, unbalance, dg_p,
                dg_ack=dg_ack,
                sim_state=cur_state,
            )
            # 7b. 变位记账 (越过死区/状态变化才往历史账本追加一条)
            hrec.yc_step(cur, yc_rows, sim_time)
            hrec.yx_step(cur, yx_rows, sim_time)

            # 8. 控制台打印 (每秒一次太密, 仅每 5s 打一次)
            if sim_time - last_print_t >= 5:
                last_print_t = sim_time
                t_real = time.time() - started
                speed_real = sim_time / t_real if t_real > 0 else 0
                print(
                    f'  t={sim_time:4d}s  '
                    f'wind={wind:5.1f}m/s  load={load_kw:6.1f}kW  '
                    f'wt={wt_act:6.1f}kW(pitch={wt_pitch:4.1f}°)  '
                    f'dg={dg_act:6.1f}kW  unbal={unbalance:+6.1f}kW  '
                    f'(real {speed_real:.2f}x)',
                )

            # 8. 结束条件: CLI --until 或 UI 设的 sim_end_s
            sim_end_s = int(get_param(cur, 'sim_end_s', '0') or 0)
            if (until and sim_time >= until) or (sim_end_s > 0 and sim_time >= sim_end_s):
                print(f'\n[OK] 已跑到 t={sim_time}s, 自动结束')
                break

    except KeyboardInterrupt:
        print('\n[!] Ctrl+C, 停止仿真')

    finally:
        if server:
            server._running = False   # 通知总机线程收尾(daemon, 不强等, 随进程退出)
        set_param(cur, 'sim_state', '0')
        # 退出时也必须把状态灯 R03:1 熄灭 —— 否则 UI 顶部/左栏会一直亮着
        # 绿灯"仿真运行", 而仿真早停了(2026-09-11 修)
        try:
            cur.execute(
                "UPDATE yx_realtime SET value=0, "
                "updated_at=datetime('now','localtime') "
                "WHERE rtu_id='R03' AND point_no=1")
        except Exception:
            pass
        db_log('INFO', 'MAIN', '仿真进程退出 sim=%ss' % sim_time)
        conn.close()
        print(f'\n仿真停止, sim_time={sim_time}s')
        print(f'提示: 看 sim_history 表的落数据情况')
        print(f'      sqlite> SELECT count(*) FROM sim_history;')
        print(f'      sqlite> SELECT * FROM sim_history ORDER BY sim_time DESC LIMIT 5;')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', default=None,
                    help='场景 CSV 路径, 例如 scenarios/02_适宜风速_风机满发.csv')
    ap.add_argument('--speed', type=float, default=1.0,
                    help='仿真倍速, 1=实时, 10=10倍速(快速跑数据)')
    ap.add_argument('--until', type=int, default=None,
                    help='跑到 t=N 秒自动停, 不写就一直跑')
    ap.add_argument('--comm', dest='comm', action='store_true', default=True,
                    help='启动通讯总机(默认开; 仿真+总机同一进程)')
    ap.add_argument('--no-comm', dest='comm', action='store_false',
                    help='只跑仿真, 不开通讯总机')
    ap.add_argument('--port', type=int, default=9000,
                    help='通讯总机端口 (默认 9000)')
    ap.add_argument('--loop', dest='loop', action='store_true', default=True,
                    help='场景循环(默认开; 跑超 env_curve 末尾自动折回起点)')
    ap.add_argument('--no-loop', dest='loop', action='store_false',
                    help='场景不循环(钳在末尾帧, 用于单工况精细观察)')
    args = ap.parse_args()
    run_loop(args.scenario, args.speed, args.until,
             comm=args.comm, port=args.port, loop=args.loop)

if __name__ == '__main__':
    main()
