from database import SessionLocal
from models import YCInfo


session=SessionLocal()

print(
    session.query(YCInfo).count()
)

session.close()