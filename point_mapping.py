
POINT_CODE_MAP = {
    "W_SPD": "wind_speed",
    "WT_ACT": "wind_actual_power",
    "WT_PITCH": "wind_pitch_angle",
    "WT_AVAIL": "wind_available_power",
    "WT_P_SET_ACK": "wind_setpoint_feedback",

    "DG_ACT": "diesel_actual_power",
    "DG_P_SET_ACK": "diesel_setpoint_feedback",
    "DG_LOAD": "diesel_load_rate",

    "LD_ACT": "load_power",
    "UNBAL": "power_unbalance",
    "GRID_FREQ": "grid_frequency",

    "WT_RUN": "wind_running",
    "WT_FAULT": "wind_fault",
    "WIFI_STA": "wind_wifi_status",

    "DG_RUN": "diesel_running",
    "DG_ALARM": "diesel_alarm",

    "SIM_RUN": "simulation_status",
    "COMM_EMS": "ems_comm_status",
    "COMM_WT": "wind_comm_status",

    "WT_START": "wind_start_command",
    "DG_START": "diesel_start_command",

    "WT_P_SET": "wind_power_setpoint",
    "WT_PITCH_SET": "wind_pitch_setpoint",
    "DG_P_SET_CMD": "diesel_power_setpoint",
    "DG_START":("R02","YK",1),
}


INTERNAL_TO_CODE = {
    v: k
    for k, v in POINT_CODE_MAP.items()
}


