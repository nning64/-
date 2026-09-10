
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


# ==================================================
# 数据库路径
# ==================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DB_PATH = os.path.join(
    BASE_DIR,
    "ems.db"
)


DATABASE_URL = (
    "sqlite:///"
    + DB_PATH.replace("\\", "/")
)


# ==================================================
# SQLAlchemy Engine
# ==================================================

engine = create_engine(
    DATABASE_URL,

    # SQLite 多线程支持
    connect_args={
        "check_same_thread": False
    },

    echo=False
)


# ==================================================
# Session
# ==================================================

SessionLocal = sessionmaker(
    bind=engine,

    autoflush=False,

    autocommit=False
)


# ==================================================
# ORM Base
# ==================================================

Base = declarative_base()


# ==================================================
# 初始化数据库
# ==================================================

def init_db():

    # 导入模型，让SQLAlchemy知道有哪些表

    from models import (
        RTUInfo,
        YCInfo,
        YXInfo,
        YKInfo,
        YTInfo,
        DeviceParam,
        EMSParam,
        DispatchHistory,
        SystemLog
    )


    Base.metadata.create_all(
        bind=engine
    )


if __name__ == "__main__":

    init_db()

    print(
        "EMS数据库初始化完成:"
    )

    print(
        DB_PATH
    )