from database import SessionLocal
from models import YTInfo

from database import engine
print("CHECK数据库：",engine.url)


session = SessionLocal()

try:

    items = session.query(YTInfo).all()

    print("====================")
    print("当前YT表:")
    print("====================")

    for item in items:
        print(
            "code:",
            item.code,
            "value:",
            item.value,
            "state:",
            item.state
        )

finally:
    session.close()