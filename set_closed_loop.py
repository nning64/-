from database import SessionLocal
from models import EMSParam


session = SessionLocal()

try:
    item = session.query(
        EMSParam
    ).filter(
        EMSParam.key == "ems_control_mode"
    ).first()

    if item is None:
        print("没有找到 ems_control_mode")
    else:
        print("修改前：", item.value)

        item.value = 1

        session.commit()

        print("修改后：", item.value)
        print("EMS 已切换为闭环模式")

finally:
    session.close()