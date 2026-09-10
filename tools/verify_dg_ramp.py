"""验证 ramp 改可配 (默认 0=不限) 后, DieselGen 行为正确。
- ramp=0: target 即到
- ramp=20: 每秒最多 20kW
- 热更新 ramp: update_params 后下一帧立刻生效
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.dg_model import DieselGen, DEFAULT_RAMP

print(f'DEFAULT_RAMP = {DEFAULT_RAMP}  (期望 0)')
assert DEFAULT_RAMP == 0

# ramp=0: 瞬时跟踪
gen = DieselGen({'dg_rated': 300, 'dg_p_min': 30, 'dg_p_max': 300}, ramp=0)
gen.step(200.0, run_cmd=1)
print(f'ramp=0, target=200 -> act={gen.act}  (期望 200)')
assert gen.act == 200.0

# ramp=20: 每秒最多 20
gen = DieselGen({'dg_rated': 300, 'dg_p_min': 30, 'dg_p_max': 300}, ramp=20)
gen.step(200.0, run_cmd=1)
print(f'ramp=20, target=200 第1s -> act={gen.act}  (期望 20)')
assert gen.act == 20.0
gen.step(200.0, run_cmd=1)
gen.step(200.0, run_cmd=1)
gen.step(200.0, run_cmd=1)
print(f'ramp=20, target=200 第4s -> act={gen.act}  (期望 80)')
assert gen.act == 80.0

# 热更新 ramp: 100 -> 立刻生效
gen.update_params({'dg_rated': 300, 'dg_p_min': 30, 'dg_p_max': 300, 'dg_ramp': 0})
gen.step(200.0, run_cmd=1)
print(f'热更新 ramp=0 后, target=200 -> act={gen.act}  (期望 200)')
assert gen.act == 200.0

print('\n所有断言通过 ✓')