from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean
)

from database import Base


class RTUInfo(Base):
    __tablename__ = "rtu_info"

    id = Column(Integer, primary_key=True)
    rtu_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    status = Column(Integer, default=0)
    refresh_time = Column(Integer, default=0)


class YCInfo(Base):
    __tablename__ = "rtu_yc_info"

    id = Column(Integer, primary_key=True)

    rtu_id = Column(String, nullable=False)
    point_id = Column(Integer, nullable=False)

    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)

    value = Column(Float, nullable=True)
    unit = Column(String)

    quality = Column(Integer, default=0)

    sim_time = Column(Integer, default=0)
    refresh_time = Column(Integer, default=0)


class YXInfo(Base):
    __tablename__ = "rtu_yx_info"

    id = Column(Integer, primary_key=True)

    rtu_id = Column(String, nullable=False)
    point_id = Column(Integer, nullable=False)

    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)

    value = Column(Integer, default=0)

    quality = Column(Integer, default=0)

    sim_time = Column(Integer, default=0)
    refresh_time = Column(Integer, default=0)


class YKInfo(Base):
    __tablename__ = "rtu_yk_info"

    id = Column(Integer, primary_key=True)

    rtu_id = Column(String, nullable=False)
    point_id = Column(Integer, nullable=False)

    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)

    value = Column(Integer, default=0)

    state = Column(String, default="IDLE")

    sim_time = Column(Integer, default=0)
    refresh_time = Column(Integer, default=0)


class YTInfo(Base):
    __tablename__ = "rtu_yt_info"

    id = Column(Integer, primary_key=True)

    rtu_id = Column(String, nullable=False)
    point_id = Column(Integer, nullable=False)

    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)

    value = Column(Float, default=0.0)
    unit = Column(String)

    state = Column(String, default="IDLE")

    sim_time = Column(Integer, default=0)
    refresh_time = Column(Integer, default=0)


class DeviceParam(Base):
    __tablename__ = "device_params"

    id = Column(Integer, primary_key=True)

    device = Column(String, nullable=False)
    key = Column(String, unique=True, nullable=False)

    value = Column(Float, nullable=False)
    unit = Column(String)

    source = Column(String)


class EMSParam(Base):
    __tablename__ = "ems_params"

    id = Column(Integer, primary_key=True)

    key = Column(String, unique=True, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String)


class DispatchHistory(Base):
    __tablename__ = "dispatch_history"

    id = Column(Integer, primary_key=True)

    sim_time = Column(Integer, default=0)

    load_power = Column(Float, default=0.0)

    wind_available = Column(Float, default=0.0)
    wind_set = Column(Float, default=0.0)

    diesel_set = Column(Float, default=0.0)

    power_shortage = Column(Float, default=0.0)
    power_excess = Column(Float, default=0.0)

    control_mode = Column(Integer, default=0)

    create_time = Column(Integer, default=0)


class SystemLog(Base):
    __tablename__ = "system_log"

    id = Column(Integer, primary_key=True)

    level = Column(String, default="INFO")

    module = Column(String)

    message = Column(String)

    sim_time = Column(Integer, default=0)

    create_time = Column(Integer, default=0)

class Evaluation(Base):

    __tablename__ = "evaluation"


    id = Column(
        Integer,
        primary_key=True
    )


    sim_time = Column(
        Integer
    )


    object = Column(
        String
    )


    indicator = Column(
        String
    )


    score = Column(
        Float
    )


    description = Column(
        String
    )


    create_time = Column(
        Integer
    )