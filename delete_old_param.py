from database import SessionLocal
from models import DeviceParam


session = SessionLocal()


old = session.query(DeviceParam).filter(
    DeviceParam.key.in_(
        [
            "dg_p_min",
            "dg_p_max"
        ]
    )
).all()


for p in old:
    print(
        "删除:",
        p.device,
        p.key,
        p.value
    )
    session.delete(p)


session.commit()

print("删除完成")

session.close()