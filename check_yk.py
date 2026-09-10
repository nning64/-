from database import SessionLocal
from models import YKInfo


session = SessionLocal()


print("====================")
print("当前YK表:")
print("====================")


data = session.query(
    YKInfo
).all()


for item in data:

    print(
        "code:",
        item.code,
        "value:",
        item.value,
        "state:",
        item.state
    )


session.close()