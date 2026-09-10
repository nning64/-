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
"""

import csv
import os

# 风机默认参数(用于合理性校验)
CUT_IN_WIND = 3.0
RATED_WIND = 12.0
CUT_OUT_WIND = 25.0
RATED_POWER_KW = 100.0

# 柴发默认参数
DG_P_MAX = 200.0   # kW 上限
DG_P_MIN = 20.0    # kW 下限

# 负荷典型值
LOAD_LOW = 30.0
LOAD_MID = 80.0
LOAD_HIGH = 250.0

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
    print(f"  ✓ {name:<32s}  {len(rows):>4d} 行  →  {os.path.relpath(path)}")


# ---------- 6 个验收场景 ----------

print("\n生成 6 个验收标准场景 →", OUT_DIR, "\n")

# 01 无风:风速 < 切入,风机停发,柴发承担全部负荷
write_csv("01_无风_风机停发", series_const(60, 1.5, LOAD_MID))

# 02 适宜风速:风速=额定,风机满发 100kW
write_csv("02_适宜风速_风机满发", series_const(60, RATED_WIND, 60.0))

# 03 高风速:接近切出(未触发切出),风机仍按额定
write_csv("03_高风速_接近切出", series_const(60, 22.0, LOAD_MID))

# 04 低负荷:负荷 < 风机出力,柴发可能被压到 P_min 以下
write_csv("04_低负荷_柴发接近下限", series_const(60, 10.0, LOAD_LOW))

# 05 高负荷:超过风机+柴发联合出力,触发柴发夹上限
write_csv("05_高负荷_柴发夹上限", series_const(60, RATED_WIND, LOAD_HIGH))

# 06 柴发上下限综合验证:负荷 0→350 渐变,看柴发下限保底与上限封顶
write_csv(
    "06_柴发上下限_负荷渐变",
    series_ramp(
        120,
        wind_pts=[(0, 10.0), (120, 10.0)],
        load_pts=[(0, 0.0), (30, 50.0), (60, 150.0), (90, 280.0), (120, 350.0)],
    ),
)

print(
    "\n6 个场景已生成。抽样检查:\n"
    "  head -n 5 scenarios/02_适宜风速_风机满发.csv\n"
    "  wc -l scenarios/*.csv\n"
)
