from database import SessionLocal
from models import YKInfo


session = SessionLocal()


yk = session.query(
    YKInfo
).filter(
    YKInfo.code=="DG_START"
).first()


if yk:

    yk.value = 1
    yk.state = "New"

    print("已设置柴发启动")

else:

    print("没有找到DG_START")


session.commit()

session.close()