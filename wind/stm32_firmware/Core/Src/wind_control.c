#include "wind_control.h"
#include <math.h>

// 静态参数指针（便于内部调用）
static WindParams_t *p_params = NULL;
extern WindParams_t g_wind_params;   // 来自 main.c
extern uint8_t g_control_mode;


void WindControl_Init(WindState_t *state, WindParams_t *params)
{
    p_params = params;
    state->wind_speed = 0;
    state->power_setpoint = 0;
    state->avail_power = 0;
    state->output_power = 0;
    state->pitch_angle = 0;
    state->run_status = 0;
}

void WindControl_Update(WindState_t *state)
{
    float wind = state->wind_speed;
    float set = state->power_setpoint;
    float avail = 0;
    float pitch = 0;
    uint8_t run = 0;

    // 1. 计算可用功率（风机能量捕获模型）—— 与 A 端共用同一公式
    //      v ≤ rated_wind : avail = P_rated × (v / v_rated)³   （风能 ∝ v³）
    //      v > rated_wind : avail = P_rated                     （发电机封顶）
    //      clamp 到 [0, P_rated]，对应 A 端 _clamp(avail, 0.0, p)
    //    切入/切出风速是 C 端的启停保护（A 端模型未含）：停机时靠
    //    pitch = max_pitch 把 A 侧出力压到 0，两边出力仍然对齐。
    if (wind < p_params->cut_in || wind > p_params->cut_out) {
        avail = 0;
        run = 0;
    } else {
        if (p_params->rated_wind > 0.0f && wind <= p_params->rated_wind) {
            float r = wind / p_params->rated_wind;   // 无量纲风速比
            avail = p_params->rated_power * r * r * r;   // 用连乘代替 powf，省算力
        } else {
            avail = p_params->rated_power;
        }
        // clamp，与 A 端 _clamp(avail, 0.0, p) 对齐
        if (avail < 0.0f) avail = 0.0f;
        if (avail > p_params->rated_power) avail = p_params->rated_power;
        run = 1;
    }

    // 2. 桨距角计算（变桨限功率算法）—— 先于 output 算出，本周期使用
    //    物理语义：output = avail × (1 − β/30)  →  β = max_pitch × (1 − set/avail)
    //      set = avail (满发指令)  → β = 0  (全迎风)
    //      set = 0   (完全卸荷)    → β = max_pitch (顺桨)
    //      set ∈ (0, avail)        → 线性插值
    if (run == 1 && avail > 0) {
        if (set >= avail) {
            // A 端设定值 ≥ 可用功率：全迎风满发，不变桨
            pitch = 0;
        } else if (set <= 0) {
            // A 端设定值 ≤ 0（要求卸荷/停机）：全顺桨，output 自然归零
            pitch = p_params->max_pitch;
        } else {
            // 0 < set < avail：按比例顺桨
            // 分母用 avail（自适应），不用 rated_power（旧算法在低风速段会让
            // β 偏小，C 预测的出力 > A 实测，原因是低风速时 avail << rated_power，
            // 但 (avail-set) 与 rated_power 比值仍会算出大 β）
            pitch = p_params->max_pitch * (1.0f - set / avail);
            if (pitch < 0) pitch = 0;
            if (pitch > p_params->max_pitch) pitch = p_params->max_pitch;
        }
    } else {
        // 停机 / 无风：桨叶顺桨到最大（卸荷保护姿态），output 自然为 0
        pitch = p_params->max_pitch;
    }

    // 3. 实际输出功率 = 可用功率 × η(桨距)，η = 1 − β/30
    //    β=0° (全迎风) → 出力 100%；β=max_pitch (全卸荷) → 出力 0
    //    该公式与 A 端相同，保证 C 的预测出力与 A 的实测出力对齐
    float output = (run == 1 && avail > 0)
                 ? (avail * (p_params->max_pitch - pitch) / p_params->max_pitch)
                 : 0.0f;

    // 4. 写回状态结构体
    state->avail_power = avail;
    state->output_power = output;
    state->pitch_angle = pitch;
    state->run_status = run;
}
