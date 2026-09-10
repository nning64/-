from database import SessionLocal
from models import DeviceParam


session = SessionLocal()


item = session.query(DeviceParam).filter(
    DeviceParam.key=="DG_P_MIN"
).first()


if item:

    item.value = 40

else:

    item = DeviceParam(
        device="DG",
        key="DG_P_MIN",
        value=40,
        unit="kW",
        source="EMS"
    )

    session.add(item)


session.commit()

print("DG_P_MIN 修复完成")

session.close()