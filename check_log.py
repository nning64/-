from database import SessionLocal
from models import SystemLog


session=SessionLocal()


print("ERROR:",
      session.query(SystemLog)
      .filter(SystemLog.level=="ERROR")
      .count())


print("WARN:",
      session.query(SystemLog)
      .filter(SystemLog.level=="WARN")
      .count())


print("ALL:",
      session.query(SystemLog).count())


session.close()