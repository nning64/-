import time
import random

from database import SessionLocal

from models import (
    YCInfo,
    YXInfo
)


def update_yc(
        session,
        code,
        value,
        sim_time
):

    point = session.query(YCInfo).filter(
        YCInfo.code == code
    ).first()

    if point is None:
        return

    point.value = value

    point.quality = 0

    point.sim_time = sim_time

    point.refresh_time = int(
        time.time() * 1000
    )


def update_yx(
        session,
        code,
        value,
        sim_time
):

    point = session.query(YXInfo).filter(
        YXInfo.code == code
    ).first()

    if point is None:
        return

    point.value = value

    point.quality = 0

    point.sim_time = sim_time

    point.refresh_time = int(
        time.time() * 1000
    )


def main():

    print("==============================")
    print("模拟 A 实时数据")
    print("==============================")

    sim_time = 0

    while True:

        session = SessionLocal()

        try:

            sim_time += 1

            wind_speed = random.uniform(
                5,
                15
            )

            load_power = random.uniform(
                80,
                250
            )

            wind_available = min(
                100,
                max(
                    0,
                    wind_speed / 12 * 100
                )
            )

            wind_actual = wind_available * 0.9

            diesel_actual = max(
                0,
                load_power - wind_actual
            )

            diesel_actual = min(
                diesel_actual,
                300
            )

            update_yc(
                session,
                "W_SPD",
                round(wind_speed, 2),
                sim_time
            )

            update_yc(
                session,
                "WT_AVAIL",
                round(wind_available, 2),
                sim_time
            )

            update_yc(
                session,
                "WT_ACT",
                round(wind_actual, 2),
                sim_time
            )

            update_yc(
                session,
                "LD_ACT",
                round(load_power, 2),
                sim_time
            )

            update_yc(
                session,
                "DG_ACT",
                round(diesel_actual, 2),
                sim_time
            )

            update_yx(
                session,
                "WT_RUN",
                1,
                sim_time
            )

            update_yx(
                session,
                "WT_FAULT",
                0,
                sim_time
            )

            update_yx(
                session,
                "DG_RUN",
                1,
                sim_time
            )

            update_yx(
                session,
                "SIM_RUN",
                1,
                sim_time
            )

            session.commit()

            print(
                f"[{sim_time}s] "
                f"风速={wind_speed:.2f} m/s | "
                f"负荷={load_power:.2f} kW | "
                f"风机可用={wind_available:.2f} kW"
            )

        except Exception as e:

            session.rollback()

            print(
                "模拟数据错误：",
                e
            )

        finally:

            session.close()

        time.sleep(1)


if __name__ == "__main__":

    main()