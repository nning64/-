"""
core/dg_model.py - 柴发(柴油发电机)模型 (P1-2)

一台"有身体"的柴油发电机: 记住上一秒出力, 每秒走一步。
三个脾气(真实柴油机的物理限制):
  1. 出力范围 [p_min, p_max]    -> 低于下限燃烧不稳, 高于上限超载(读 device_params: DG 30~300kW)
  2. 爬坡率 RAMP kW/s           -> 出力只能慢慢变, 不能"啪"地跳变(油门/调速器有惯性)
  3. 启停指令                    -> 叫停: 出力先平滑降到 0 再停; 起动: 从 0 慢慢爬上去

"目标出力"从哪来:
  - 开发期(P1): main.py 用"就地补缺 = 负荷 - 风机出力"喂进来(柴发自己试图平衡)
  - 联调期(P2): EMS(同学B)通过遥调指令直接给目标
  两种来源对模型来说都是同一个 setpoint, 身体不用改。
"""

DEFAULT_RAMP = 0.0     # kW/s: 出力爬坡率(0=不限,瞬时跟踪;从 device_params dg_ramp 可配)
EPS = 1e-6              # 判断"出力已到 0"的误差


class DieselGen:
    """柴发模型。整场仿真只构造一次, 每秒调一次 step(), 内部记住上一秒状态。"""

    def __init__(self, params: dict, ramp: float = DEFAULT_RAMP):
        # params 来自 device_params 表 DG 行: dg_rated / dg_p_min / dg_p_max
        self.p_rated = params.get('dg_rated', 300.0)
        self.p_min   = params.get('dg_p_min', 30.0)
        self.p_max   = params.get('dg_p_max', 300.0)
        self.ramp    = ramp
        self.act     = 0.0   # 当前出力 kW (停机 = 0)
        self.run     = 0     # 0 停 / 1 运行(或正在起停途中)

    def update_params(self, params):
        """热更新标定参数(UI 改 device_params 后主循环每帧调一次)。
        改上限/额定/爬坡率, 不动当前出力 act 与 run 状态。
        ramp=0 意味着"无爬坡约束, 出 target 即到" —— 联调期消除限功率带来的
        unbalance 偏差, 需要时再把 device_params dg_ramp 改成非 0 即可恢复。"""
        self.p_rated = float(params.get('dg_rated', self.p_rated))
        self.p_min   = float(params.get('dg_p_min', self.p_min))
        self.p_max   = float(params.get('dg_p_max', self.p_max))
        self.ramp    = float(params.get('dg_ramp', self.ramp))

    def step(self, setpoint: float, run_cmd: int = 1) -> dict:
        """每秒走一步。
        setpoint: 目标出力 kW(将来 EMS 遥调给; 现在是"就地补缺"喂进来的)
        run_cmd : 0 = 要求停机 / 1 = 允许运行
        返回 {'p_act': 当前出力 kW, 'run': 0/1}
        """
        # 1. 定目标: 停机令 -> 目标 0; 运行令 -> 目标夹在 [p_min, p_max]
        if run_cmd == 0:
            target = 0.0
        else:
            target = max(self.p_min, min(self.p_max, setpoint))

        # 2. 朝目标爬坡, 每秒最多变 ramp; ramp=0 表示无限制, 直接到 target
        diff = target - self.act
        if self.ramp > 0 and diff > self.ramp:
            self.act += self.ramp
        elif self.ramp > 0 and diff < -self.ramp:
            self.act -= self.ramp
        else:
            self.act = target    # ramp=0 或 |diff| <= ramp: 一步到位

        # 3. 状态判定: 出力降到 0 才算真正停下; 运行令下就算爬坡途中也算"活着"
        if run_cmd == 0 and self.act <= EPS:
            self.run = 0
        elif run_cmd == 1:
            self.run = 1

        return {'p_act': self.act, 'run': self.run}
