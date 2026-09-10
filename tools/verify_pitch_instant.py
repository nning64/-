"""验证 2026-09-10 删掉桨距伺服限速后:
1. C 发桨距 -> A 立即到位(不再 6°/s 慢慢转)
2. C 按 η=set/avail 反解桨距时, A 实测出力精确等于设定值
3. A 自演模式: 桨距立即收 0 度
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.wind_model import WindTurbine, PITCH_FEATHER

P = {'cut_in_wind': 3.0, 'rated_wind': 12.0, 'cut_out_wind': 25.0, 'rated_power': 100.0}

# 1) C 接管: 发桨距 12 度 -> 第一帧就该是 12, 不再一帧 6 度
wt = WindTurbine(P)
r = wt.step(12.0, pitch_set=12.0)
print(f'[1] C 发 12°, 第 1 帧桨距 = {r["pitch"]}  (期望 12, 旧逻辑会是 6)')
assert r['pitch'] == 12.0, r

# 2) C 正确反解 -> 出力精确贴住设定
print('\n[2] C 按 η=set/avail 反解, A 实测出力应精确=设定:')
for avail_v, set_kw in [(12.0, 60.0), (10.0, 50.0), (8.0, 20.0), (6.0, 10.0)]:
    wt = WindTurbine(P)
    avail = 100.0 * (avail_v / 12.0) ** 3   # 与模型同式
    eta = set_kw / avail
    beta = PITCH_FEATHER * (1.0 - eta)      # C 端正确反解
    r = wt.step(avail_v, pitch_set=beta)
    print(f'    v={avail_v:>4} avail={avail:>6.2f} set={set_kw:>5.1f} '
          f'β={beta:>6.2f} -> act={r["p_act"]:>7.2f}  err={r["p_act"]-set_kw:+.3f}')
    assert abs(r['p_act'] - set_kw) < 0.01, r

# 3) A 自演: 桨距立即收 0 度
wt = WindTurbine(P)
r = wt.step(12.0)   # 无 pitch_set
print(f'\n[3] A 自演 第 1 帧桨距 = {r["pitch"]}  (期望 0, 旧逻辑会是 84)')
assert r['pitch'] == 0.0, r

# 4) 停机 -> 立即顺桨 90
wt = WindTurbine(P)
wt.step(12.0)          # 先运行
r = wt.step(1.0)       # 风速掉到切入以下
print(f'[4] 风速低于切入 -> 桨距 = {r["pitch"]}  (期望 90)')
assert r['pitch'] == 90.0, r

print('\n全部通过: 桨距无伺服限速, 立即到位; C 正确反解时出力精确贴合。')
