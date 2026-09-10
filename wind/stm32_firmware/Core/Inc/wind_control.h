#ifndef INC_WIND_CONTROL_H_
#define INC_WIND_CONTROL_H_

#include <stdint.h>

// 风机参数结构体（从数据库/设备参数表同步）
typedef struct {
    float cut_in;      // 切入风速 (m/s)
    float rated_wind;  // 额定风速 (m/s)
    float cut_out;     // 切出风速 (m/s)
    float rated_power; // 额定功率 (kW)
    float max_pitch;   // 最大桨距角 (度)
} WindParams_t;

// 风机运行状态结构体（输入+输出）
typedef struct {
    // 输入（来自电网或传感器）
    float wind_speed;      // 实时风速 (m/s)
    float power_setpoint;  // EMS 下发的功率设定 (kW)
    
    // 输出（计算结果）
    float avail_power;     // 可用功率 (kW)
    float output_power;    // 实际输出功率 (kW)
    float pitch_angle;     // 目标桨距角 (度)
    uint8_t run_status;    // 启停状态 (0停止, 1运行)
} WindState_t;

// ---- 对外接口函数 ----
void WindControl_Init(WindState_t *state, WindParams_t *params);
void WindControl_Update(WindState_t *state);

#endif