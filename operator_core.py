import time

from database import SessionLocal

from models import (
    YCInfo,
    YTInfo,
    DeviceParam,
    EMSParam,
    DispatchHistory,
    SystemLog
)
from evaluation import evaluate_ems
from models import EMSParam
def get_yc(session, code):

    item = session.query(
        YCInfo
    ).filter(
        YCInfo.code == code
    ).order_by(
        YCInfo.refresh_time.desc()
    ).first()


    if item:

        return item.value

    return None


def get_device_param(session,key):

    param = session.query(DeviceParam).filter(
        DeviceParam.key == key
    ).first()

    if param is None:

        param = session.query(DeviceParam).filter(
            DeviceParam.key.ilike(key)
        ).first()


    if param is None:
        return None

    return param.value
def get_ems_param(session,key):

    param = session.query(EMSParam).filter(
        EMSParam.key == key
    ).first()

    if param is None:
        return None

    return param.value

def set_yt(
        session,
        code,
        value,
        sim_time,
        control_mode
):

    point = session.query(YTInfo).filter(
        YTInfo.code == code
    ).first()

    if point is None:
        return

    point.value = value

    point.sim_time = sim_time

    point.refresh_time = int(
        time.time() * 1000
    )
    if int(control_mode)==1:
        point.state="New"
    else:
        point.state="CALC"




def add_log(
        session,
        level,
        message,
        sim_time=0
):

    log = SystemLog(

        level=level,

        module="operator_core",

        message=message,

        sim_time=sim_time,

        create_time=int(
            time.time() * 1000
        )
    )

    session.add(log)


def dispatch():

    session = SessionLocal()

    try:

        load_power = get_yc(
            session,
            "LD_ACT"
        )

        wind_available = get_yc(
            session,
            "WT_AVAIL"
        )

        dg_p_max = get_device_param(
            session,
       "DG_P_MAX"
        )

        dg_p_min = get_device_param(
            session,
       "Dg_P_MIN"
        )

        control_mode = get_ems_param(
            session,
            "ems_control_mode"
        )
        print("===================")
        print("调试参数")
        print("LD_ACT:", load_power)
        print("WT_AVAIL:", wind_available)
        print("DG_MIN:", dg_p_min)
        print("DG_MAX:", dg_p_max)
        print("===================")
        if (
            load_power is None
            or wind_available is None
            or dg_p_max is None
            or dg_p_min is None
        ):

            print(
                "实时数据不完整，暂不调度"
            )

            add_log(
                session,
                "WARN",
                "实时数据不完整，跳过本次调度"
            )

            session.commit()

            return

        load_power = max(
            0,
            load_power
        )

        wind_available = max(
            0,
            wind_available
        )
        print("==============================")
        print("调度输入检查")
        print("负荷 LD_ACT:", load_power)
        print("风电可用 WT_AVAIL:", wind_available)
        print("柴发最小:", dg_p_min)
        print("柴发最大:", dg_p_max)
        print("控制模式:", control_mode)
        print("==============================")

        # ==========================
        # 风电优先
        # ==========================

        wind_set = min(
            load_power,
            wind_available
        )

        remaining = max(
            0,
            load_power - wind_available
        )

        # ==========================
        # 柴发补偿
        # ==========================

        if remaining <= 0:

            diesel_set = dg_p_min
            wind_set=max(
                0,
                load_power-diesel_set
            )
            wind_set=min(
                wind_set,
                wind_available,
                load_power
            )

        elif remaining < dg_p_min:

            diesel_set = dg_p_min

            wind_set = max(
                0,
                load_power
                - diesel_set
            )

            wind_set = min(
                wind_set,
                wind_available
            )

        else:

            diesel_set = min(
                remaining,
                dg_p_max
            )

        total_generation = (
            wind_set
            + diesel_set
        )

        power_shortage = max(
            0,
            load_power
            - total_generation
        )

        power_excess = max(
            0,
            total_generation
            - load_power
        )

        sim_time_point = session.query(
            YCInfo
        ).filter(
            YCInfo.code == "LD_ACT"
        ).first()

        if sim_time_point:

            sim_time = (
                sim_time_point.sim_time
            )

        else:

            sim_time = 0

        # ==========================
        # 保存调度结果
        # ==========================

        set_yt(
            session,
            "WT_P_SET",
            round(wind_set, 2),
            sim_time,
            control_mode
        )

        set_yt(
            session,
            "DG_P_SET_CMD",
            round(diesel_set, 2),
            sim_time,
            control_mode
        )

        history = DispatchHistory(

            sim_time=sim_time,

            load_power=load_power,

            wind_available=wind_available,

            wind_set=wind_set,

            diesel_set=diesel_set,

            power_shortage=power_shortage,

            power_excess=power_excess,

            control_mode=int(
                control_mode
            ),

            create_time=int(
                time.time() * 1000
            )
        )

        session.add(history)
        session.commit()
        evaluate_ems(

            session,

            sim_time,

            load_power,

            wind_available,

            wind_set,

            diesel_set

        )
        text = (
            f"负荷={load_power:.2f} kW | "
            f"风机可用={wind_available:.2f} kW | "
            f"风机设定={wind_set:.2f} kW | "
            f"柴发设定={diesel_set:.2f} kW"
        )

        if power_shortage > 0:

            text += (
                f" | 功率缺口="
                f"{power_shortage:.2f} kW"
            )

        if power_excess > 0:

            text += (
                f" | 功率过剩="
                f"{power_excess:.2f} kW"
            )

        add_log(
            session,
            "INFO",
            text,
            sim_time
        )

        session.commit()

        mode_text = (
            "闭环"
            if int(control_mode) == 1
            else "开环"
        )

        print(
            "------------------------------"
        )

        print(
            f"EMS 调度时刻：{sim_time}s"
        )

        print(
            f"控制模式：{mode_text}"
        )

        print(
            f"负荷：{load_power:.2f} kW"
        )

        print(
            f"风机可用："
            f"{wind_available:.2f} kW"
        )

        print(
            f"风机设定："
            f"{wind_set:.2f} kW"
        )

        print(
            f"柴发设定："
            f"{diesel_set:.2f} kW"
        )

        if power_shortage > 0:

            print(
                f"供电不足："
                f"{power_shortage:.2f} kW"
            )

        if int(control_mode) == 0:

            print(
                "开环：仅计算并保存，不发送"
            )

        else:

            print(
                "闭环：指令已写入 YT 表，"
                "等待 operator_io 发送"
            )

    except Exception as e:

        session.rollback()

        print(
            "调度错误：",
            e
        )

    finally:

        session.close()


def main():

    print("==============================")
    print("EMS operator_core 启动")
    print("==============================")

    while True:

        dispatch()

        time.sleep(5)


if __name__ == "__main__":

    main()