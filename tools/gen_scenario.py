#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微电网大作业 - 电网模拟器场景数据生成器
==========================================
输出验收要求的 6 个标准场景(CSV),供电网模拟器加载做功率平衡仿真。

输出位置:../scenarios/<场景名>.csv
文件格式:每行 (t, wind_speed_m_s, load_kw)  ,1s 步长
默认时长:60~120 秒

用法:
    python gen_scenario.py
    # 直接运行,在 scenarios/ 目录写 6 个 CSV

注意:
- 这些场景仅用于开发/联调,不是真实气象数据
- 风机/柴发参数(切入/额定/切出、P_max/P_min)只用于生成场景时的合理性校验,
  实际跑仿真时以 device_params 表为准

设计原则(2026-09-10 重写):
- 所有 6 个场景的风速都用 ramp —— 风速常量场景对风机模型没有验证意义
  (A 端风速一旦能跑出有意义的功率曲线,验证才是真验证)
- 05 高负荷: LOAD_HIGH 由 250 提到 380 —— 风机满发 100 + 柴发上限 200 = 300,
  load 要 > 300 才能看到柴发夹上限现象(load=250 时柴发目标 150,根本没夹)
- 柴发下限与 schema.sql 对齐:DG_P_MIN=30 kW(旧 20 已废)
"""

import csv
import os

# 风机默认参数(用于合理性校验)
CUT_IN_WIND = 3.0
RATED_WIND = 12.0
CUT_OUT_WIND = 25.0
RATED_POWER_KW = 100.0

# 柴发默认参数
DG_P_MAX = 300.0   # kW 上限(与 schema dg_p_max 一致)
DG_P_MIN = 30.0    # kW 下限(与 schema dg_p_min 一致; 旧 20 已废)

# 负荷典型值
LOAD_LOW = 30.0
LOAD_MID = 80.0
LOAD_HIGH = 380.0  # ↑ 旧 250 改 380 —— 风机100+柴发200=300, 380>300 触发柴发夹上限

OUT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scenarios")
)
os.makedirs(OUT_DIR, exist_ok=True)


# ---------- 波形工具函数 ----------

def series_const(t_end: int, wind: float, load: float):
    """恒定值场景:风速和负荷都不变。"""
    return [(t, round(wind, 2), round(load, 2)) for t in range(t_end + 1)]


def series_ramp(t_end: int, wind_pts, load_pts):
    """
    分段线性插值场景。
    wind_pts / load_pts: [(t, value), ...],  按 t 升序,首尾覆盖 0~t_end
    """
    def interp(pts, t):
        for i in range(len(pts) - 1):
            t0, v0 = pts[i]
            t1, v1 = pts[i + 1]
            if t <= t0:
                return v0
            if t0 <= t <= t1:
                if t1 == t0:
                    return v0
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    rows = []
    for t in range(t_end + 1):
        rows.append((
            t,
            round(interp(wind_pts, t), 2),
            round(interp(load_pts, t), 2),
        ))
    return rows


def write_csv(name: str, rows):
    path = os.path.join(OUT_DIR, name + ".csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "wind_speed_m_s", "load_kw"])
        w.writerows(rows)
    # 统计风速/负荷唯一值数 —— 验收要求 "环境曲线不能是常量"
    wind_uniq = len({r[1] for r in rows})
    load_uniq = len({r[2] for r in rows})
    flag = "✓" if (wind_uniq > 1 and load_uniq > 1) else "✗ 常量!"
    print(f"  {flag} {name:<32s}  {len(rows):>4d} 行  wind_uniq={wind_uniq:>3d}  load_uniq={load_uniq:>3d}  →  {os.path.relpath(path)}")


# ---------- 6 个验收场景 ----------

print("\n生成 6 个验收标准场景 →", OUT_DIR, "\n")

# 01 无风: 风速全程 < 切入, 风机停发, 柴发承担全部负荷
#    风速 ramp 2.5→1.0→2.0(全段在 CUT_IN=3.0 以下)
#    负荷小幅波动 70→90, 看柴发跟随负荷爬坡
write_csv(
    "01_无风_风机停发",
    series_ramp(
        60,
        wind_pts=[(0, 2.5), (20, 1.5), (40, 0.5), (60, 2.0)],
        load_pts=[(0, 70.0), (20, 90.0), (40, 75.0), (60, 85.0)],
    ),
)

# 02 适宜风速: 风速跨过切入到额定附近, 风机应能从 0 爬升到接近满发
#    风速 ramp 2.0→6.0→12.0→10.0(从切入以下到额定)
#    负荷小幅波动 50→70, 看风机 + 柴发联合出力平衡
write_csv(
    "02_适宜风速_风机满发",
    series_ramp(
        60,
        wind_pts=[(0, 2.0), (15, 6.0), (30, 12.0), (45, 12.0), (60, 10.0)],
        load_pts=[(0, 50.0), (20, 70.0), (40, 55.0), (60, 65.0)],
    ),
)

# 03 高风速: 风速从 18 升到 22(未到切出 25), 风机钳额定 100 kW
#    风速 ramp 18→20→22, 负荷 ramp 60→80→100(看风机钳额定 + 柴发补缺变化)
write_csv(
    "03_高风速_接近切出",
    series_ramp(
        60,
        wind_pts=[(0, 18.0), (30, 20.0), (60, 22.0)],
        load_pts=[(0, 60.0), (30, 80.0), (60, 100.0)],
    ),
)

# 04 低负荷: 风速在额定附近, 负荷 60→30→20(压在 DG_P_MIN=30 上下)
#    风速 ramp 10→12, 负荷 ramp 60→30→20
write_csv(
    "04_低负荷_柴发接近下限",
    series_ramp(
        60,
        wind_pts=[(0, 10.0), (30, 11.0), (60, 12.0)],
        load_pts=[(0, 60.0), (20, 30.0), (40, 20.0), (60, 25.0)],
    ),
)

# 05 高负荷夹上限: 风速在额定附近(风机满发 ~100), 负荷 200→380→350
#    风机 100 + 柴发上限 200 = 300, 负荷 > 300 时柴发必然夹上限
#    load=380 时 dg_sp=280 → 被夹到 200, unbalance = 380-100-200 = 80(缺电,符合预期)
write_csv(
    "05_高负荷_柴发夹上限",
    series_ramp(
        60,
        wind_pts=[(0, 10.0), (20, 12.0), (40, 12.0), (60, 11.0)],
        load_pts=[(0, 200.0), (15, 300.0), (30, 380.0), (45, 380.0), (60, 350.0)],
    ),
)

# 06 综合: 负荷 0→350 渐变 + 风速小幅波动
#    看柴发下限保底(t≈10 时 load=20→风机~70→dg_sp≈-50, 钳到 30) +
#    上限封顶(末尾 load=350 时 dg_sp≈250, 仍 < 300 上限, 但要看);
#    后续若想真夹上限可把末段 load 提到 400+, 留作 03 那种"必然夹"场景做对比
write_csv(
    "06_柴发上下限_负荷渐变",
    series_ramp(
        120,
        wind_pts=[(0, 8.0), (40, 10.0), (80, 12.0), (120, 11.0)],
        load_pts=[(0, 0.0), (30, 50.0), (60, 150.0), (90, 280.0), (120, 350.0)],
    ),
)

print(
    "\n6 个场景已生成。抽样检查:\n"
    "  head -n 5 scenarios/02_适宜风速_风机满发.csv\n"
    "  wc -l scenarios/*.csv\n"
    "\n合理性校验:\n"
    "  风机 + 柴发联合出力上限 ≈ 100 + 300 = 400 kW\n"
    "  05 末尾负荷 380 应当触发柴发夹上限(场景设计目标)\n"
    "  06 末尾负荷 350 距离夹上限还差 50 kW(看 350 → 400 可触发夹上限)\n"
)