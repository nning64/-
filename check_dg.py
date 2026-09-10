from database import SessionLocal
from models import DeviceParam


session = SessionLocal()

data = session.query(DeviceParam).filter(
    DeviceParam.device=="DG"
).all()


for p in data:

    print(
        "key=",
        p.key,
        "value=",
        p.value
    )


session.close()