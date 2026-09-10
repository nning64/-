import time

from database import engine, SessionLocal, Base

from models import (
    RTUInfo,
    YCInfo,
    YXInfo,
    YKInfo,
    YTInfo,
    DeviceParam,
    EMSParam
)


def init_rtu(session):
    data = [
        ("R01", "风电机组"),
        ("R02", "柴油发电机组"),
        ("R03", "电网与负荷")
    ]

    for rtu_id, name in data:

        old = session.query(RTUInfo).filter(
            RTUInfo.rtu_id == rtu_id
        ).first()

        if old is None:

            session.add(
                RTUInfo(
                    rtu_id=rtu_id,
                    name=name,
                    status=0
                )
            )


def init_yc(session):

    points = [

        # R01 风机

        ("R01", 1, "W_SPD",
         "风速", "m/s"),

        ("R01", 2, "WT_ACT",
         "风机有功出力", "kW"),

        ("R01", 3, "WT_PITCH",
         "当前桨距角", "°"),

        ("R01", 4, "WT_AVAIL",
         "风机可用功率", "kW"),

        ("R01", 5, "WT_P_SET_ACK",
         "风机功率设定回显", "kW"),

        # R02 柴发

        ("R02", 1, "DG_ACT",
         "柴发实际出力", "kW"),

        ("R02", 2, "DG_P_SET_ACK",
         "柴发功率设定回显", "kW"),

        ("R02", 3, "DG_LOAD",
         "柴发负荷率", "%"),

        # R03 电网负荷

        ("R03", 1, "LD_ACT",
         "负荷有功功率", "kW"),

        ("R03", 2, "UNBAL",
         "系统不平衡功率", "kW"),

        ("R03", 3, "GRID_FREQ",
         "电网频率", "Hz")
    ]

    for rtu_id, point_id, code, name, unit in points:

        old = session.query(YCInfo).filter(
            YCInfo.code == code
        ).first()

        if old is None:

            session.add(
                YCInfo(
                    rtu_id=rtu_id,
                    point_id=point_id,
                    code=code,
                    name=name,
                    unit=unit,
                    quality=1
                )
            )


def init_yx(session):

    points = [

        ("R01", 1, "WT_RUN",
         "风机运行状态"),

        ("R01", 2, "WT_FAULT",
         "风机故障状态"),

        ("R01", 3, "WIFI_STA",
         "风机通信状态"),

        ("R02", 1, "DG_RUN",
         "柴发运行状态"),

        ("R02", 2, "DG_ALARM",
         "柴发告警"),

        ("R03", 1, "SIM_RUN",
         "仿真运行状态"),

        ("R03", 2, "COMM_EMS",
         "EMS通信状态"),

        ("R03", 3, "COMM_WT",
         "风机控制器通信状态")
    ]

    for rtu_id, point_id, code, name in points:

        old = session.query(YXInfo).filter(
            YXInfo.code == code
        ).first()

        if old is None:

            session.add(
                YXInfo(
                    rtu_id=rtu_id,
                    point_id=point_id,
                    code=code,
                    name=name
                )
            )


def init_yk(session):

    points = [

        ("R01", 1,
         "WT_START",
         "风机启停指令"),

        ("R02", 1,
         "DG_START",
         "柴发启停指令")
    ]

    for rtu_id, point_id, code, name in points:

        old = session.query(YKInfo).filter(
            YKInfo.code == code
        ).first()

        if old is None:

            session.add(
                YKInfo(
                    rtu_id=rtu_id,
                    point_id=point_id,
                    code=code,
                    name=name
                )
            )


def init_yt(session):

    points = [

        (
            "R01",
            1,
            "WT_P_SET",
            "风机功率设定值",
            "kW"
        ),

        (
            "R01",
            2,
            "WT_PITCH_SET",
            "风机桨距角设定值",
            "°"
        ),

        (
            "R02",
            1,
            "DG_P_SET_CMD",
            "柴发功率设定值",
            "kW"
        )
    ]

    for rtu_id, point_id, code, name, unit in points:

        old = session.query(YTInfo).filter(
            YTInfo.code == code
        ).first()

        if old is None:

            session.add(
                YTInfo(
                    rtu_id=rtu_id,
                    point_id=point_id,
                    code=code,
                    name=name,
                    unit=unit
                )
            )


def init_device_params(session):

    params = [

        # 风机参数
        # B 保存副本
        # 后面由 C 上位机同步

        (
            "WT",
            "cut_in_wind",
            3.0,
            "m/s",
            "WT_CTRL"
        ),

        (
            "WT",
            "rated_wind",
            12.0,
            "m/s",
            "WT_CTRL"
        ),

        (
            "WT",
            "cut_out_wind",
            25.0,
            "m/s",
            "WT_CTRL"
        ),

        (
            "WT",
            "rated_power",
            100.0,
            "kW",
            "WT_CTRL"
        ),

        # 柴发参数

        (
            "DG",
            "dg_rated",
            300.0,
            "kW",
            "GRID_SIM"
        ),

        (
            "DG",
            "dg_p_max",
            300.0,
            "kW",
            "GRID_SIM"
        ),

        (
            "DG",
            "dg_p_min",
            30.0,
            "kW",
            "GRID_SIM"
        )
    ]

    for device, key, value, unit, source in params:

        old = session.query(DeviceParam).filter(
            DeviceParam.key == key
        ).first()

        if old is None:

            session.add(
                DeviceParam(
                    device=device,
                    key=key,
                    value=value,
                    unit=unit,
                    source=source
                )
            )


def init_ems_params(session):

    params = [

        (
            "sample_cycle_s",
            1,
            "s"
        ),

        (
            "control_cycle_s",
            5,
            "s"
        ),

        (
            "ems_control_mode",
            0,
            ""
        )
    ]

    for key, value, unit in params:

        old = session.query(EMSParam).filter(
            EMSParam.key == key
        ).first()

        if old is None:

            session.add(
                EMSParam(
                    key=key,
                    value=value,
                    unit=unit
                )
            )


def init_database():

    print("==============================")
    print("EMS 数据库初始化")
    print("==============================")

    Base.metadata.create_all(bind=engine)

    session = SessionLocal()

    try:

        init_rtu(session)

        init_yc(session)

        init_yx(session)

        init_yk(session)

        init_yt(session)

        init_device_params(session)

        init_ems_params(session)

        session.commit()

        print("ems.db 创建成功")
        print("四遥点表初始化成功")
        print("设备参数初始化成功")
        print("EMS 参数初始化成功")

    except Exception as e:

        session.rollback()

        print("初始化失败：", e)

    finally:

        session.close()


if __name__ == "__main__":

    init_database()