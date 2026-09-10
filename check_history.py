from database import SessionLocal
from models import DispatchHistory


session = SessionLocal()


data = session.query(
    DispatchHistory
).all()


print("数量:",len(data))


for h in data[-5:]:

    print(
        h.load_power,
        h.wind_set,
        h.diesel_set
    )


session.close()