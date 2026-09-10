"""
core/wind_model.py - 风机功率模型 (A 电网模拟器)  P1-1

大白话逻辑:
    风机能发多少电 = 风速给多少能量 x 叶片吃进多少。
    按风速分 4 个区工作:
      1) v <  切入 3m/s  : 风太小 -> 停机, 桨距 90 度顺桨(叶片侧对风不转)
      2) 3 ~ 额定 12m/s : 叶片正对风(桨距向 0 度收), 尽量吃风;
                           风能 ∝ v^3, 出力随风速立方涨, 到 12m/s 吃满 100kW
      3) 额定 12 ~ 切出 25m/s: 风能已超过发电机上限, 出力死咬 100kW 不涨
                            (模型按"发电机电子封顶"处理, 不靠大幅变桨)
      4) v >= 切出 25m/s : 风太危险 -> 保护停机, 顺桨 90 度

    桨距角无惯性: 收到设定立即到位(2026-09-10 删掉伺服限速)。
    这样 A 的实测出力与 C 的预期出力逐秒严格一致,
    消除 C 接管过渡期因"A 慢慢转桨"造成的不平衡毛刺。

    桨距 -> 出力 折减(2026-09-09 起, 让 C 真实参与):
        出力 = 可用功率 x η(桨距),  η = 1 - 桨距/30  (0~30 度间线性)
        桨距 0 度 = 全迎风(η=1, 吃满可用功率)
        桨距 30 度 = 全卸荷(η=0, 出力归零)
        所以 C 想限功率: 先算 η = 设定/可用, 再反解 β = 30(1-η) 发回来。

    两种控制模式:
      A 自演 (pitch_set=None): 桨距目标是全迎风 0 度, 出力 = min(可用, EMS 限功率)。
                               C 没接 / 断发 >5s 时用这档, 保证 EMS 限功率不被突破。
      C 接管 (pitch_set=数值): 模型只听 C 的桨距设定(立即到位),
                               出力 = 可用功率 x η(桨距), A 不再钳位 EMS 限功率
                               —— 实际功率能不能贴住 EMS 设定, 完全看 C 桨距算得准不准。

用法:
    from core.wind_model import WindTurbine
    wt = WindTurbine(device_params 里读出的 WT 参数 dict)
    r  = wt.step(wind_speed)                  # A 自演
    r  = wt.step(wind_speed, pitch_set=12.0)  # C 接管: 桨距目标 12 度
    # r = {'p_avail':可用kW, 'p_act':实际kW, 'pitch':桨距deg, 'run':0/1, 'fault':0/1}
"""

# ---------------- 气动/机械标定常数 ----------------
# 想模拟不同风机就改这里; 真机应按铭牌/手册填。
AIR_DENSITY  = 1.225    # kg/m3  空气密度(标准大气)
ROTOR_RADIUS = 13.0     # m      叶轮半径 (100kW 级, 扫风面积 ~531 m2)
CP_MAX       = 0.45     #        最优风能利用系数 (Betz 理论上限 0.593)
LAMBDA_OPT   = 8.0      #        最优叶尖速比

# 桨距折减基准: β=0° 全迎风(η=1), β=PITCH_FEATHER 全卸荷(η=0), 线性。
# C 的 YT2 量程 0~30 正好覆盖"全出力 -> 归零"整个控制带。
PITCH_FEATHER = 30.0    # deg  桨距到此角度出力归零(线性折减 η=1-β/30)

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


class WindTurbine:
    """一台有记忆的风机。每秒调 step() 一次, 推进一秒钟的仿真。"""

    def __init__(self, p):
        """
        p: 从 device_params 读出的 WT 参数 dict, 必须含 4 键:
            cut_in_wind 切入风速   rated_wind 额定风速
            cut_out_wind 切出风速  rated_power 额定功率kW
        """
        self.cut_in   = p['cut_in_wind']
        self.rated    = p['rated_wind']
        self.cut_out  = p['cut_out_wind']
        self.p_rated  = p['rated_power']

        self.pitch    = 90.0   # 当前桨距: 初始=顺桨停机位
        self.run      = 0      # 上一秒运行状态
        self.fault    = 0

    def update_params(self, p):
        """热更新标定参数(UI 改 device_params 后主循环每帧调一次)。
        只改数值, 不动状态(pitch/run) —— 这样改完立即生效且不打断仿真。"""
        self.cut_in   = float(p.get('cut_in_wind',   self.cut_in))
        self.rated    = float(p.get('rated_wind',    self.rated))
        self.cut_out  = float(p.get('cut_out_wind',  self.cut_out))
        self.p_rated  = float(p.get('rated_power',   self.p_rated))

    # ---------- 核心: 每秒推进一秒 ----------
    def step(self, wind_speed, p_set=None, start_cmd=None, pitch_set=None):
        """
        wind_speed: 这一秒的风速 m/s
        p_set:      EMS 限功率指令 kW (YT1), None=不限
        start_cmd:  启停指令 1/0 (遥控 YK), None=由风速自动决定
        pitch_set:  C 桨距角设定 deg (YT2), None = A 自演模式
                    数值 = C 接管: 桨距立即到位(无伺服限速),
                    出力 = 可用功率 x η(桨距), 不再电子钳位 p_set。
        返回 dict: p_avail/p_act/pitch/run/fault
        """
        p = self.p_rated
        v = wind_speed

        # 1) 该不该转? 风速不在工作区, 或有人下令停机 -> 停机顺桨
        wind_ok = (self.cut_in <= v < self.cut_out)
        want_run = (start_cmd == 1) or (start_cmd is None and wind_ok)
        if not want_run:
            self._move_pitch(90.0)     # 立即顺桨
            self.run = 0
            return {'p_avail': 0.0, 'p_act': 0.0, 'pitch': self.pitch,
                    'run': 0, 'fault': 0}

        # 2) 运行中: 先算"这秒风速最多能吃多少"(可用功率, 0°全迎风 MPPT)
        if v <= self.rated:
            avail = p * (v / self.rated) ** 3     # 欠额定: 风能 ∝ v^3
        else:
            avail = p                              # 额定以上: 发电机封顶
        avail = _clamp(avail, 0.0, p)

        # 3) 这秒桨距该朝哪个角度走: C 接管听 C 的(0~30 有效带),
        #    A 自演则全迎风收 0 度
        if pitch_set is not None:
            pitch_target = _clamp(pitch_set, 0.0, 90.0)
        else:
            pitch_target = 0.0
        self._move_pitch(pitch_target)

        # 4) 实际出力 = 可用功率 x 桨距折减(顺桨越多, 吃得越少)
        eta = _clamp(1.0 - self.pitch / PITCH_FEATHER, 0.0, 1.0)
        act = avail * eta
        if pitch_set is None and p_set is not None:
            # A 自演: EMS 限功率由 A 电子钳位兜底(把功率压到设定内)。
            # C 接管: 不钳位 —— 内层闭环要演示的正是"C 靠桨距自己贴住设定"。
            act = min(act, max(0.0, p_set))

        self.run = 1
        return {'p_avail': round(avail, 3), 'p_act': round(act, 3),
                'pitch': round(self.pitch, 2), 'run': 1, 'fault': 0}

    # ---------- 内部: 桨距直接到位(无伺服限速) ----------
    def _move_pitch(self, target):
        self.pitch = _clamp(target, 0.0, 90.0)
