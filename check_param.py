
from database import SessionLocal
from models import DeviceParam


session = SessionLocal()


params = session.query(
    DeviceParam
).all()


print("====================")
print("当前设备参数")
print("====================")


for p in params:

    print(
        p.__dict__
    )


session.close()