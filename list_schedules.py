
from app.database import SessionLocal
from app.models.schedule_models import ScheduleModel

def list_schedules():
    db = SessionLocal()
    try:
        schedules = db.query(ScheduleModel).all()
        print(f"Found {len(schedules)} schedules:")
        for s in schedules:
            print(f"ID: {s.id}, Name: {s.name}, Type: {s.type}, Target: {s.target_id}")
    finally:
        db.close()

if __name__ == "__main__":
    list_schedules()
