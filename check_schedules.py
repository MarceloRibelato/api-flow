from app.database import SessionLocal
from app.models.schedule_models import ScheduleModel
from datetime import datetime
import json

db = SessionLocal()
try:
    schedules = db.query(ScheduleModel).all()
    print(f"Total schedules: {len(schedules)}")
    
    for s in schedules:
        next_run = s.next_run.strftime("%Y-%m-%d %H:%M:%S") if s.next_run else "None"
        last_run = s.last_run.strftime("%Y-%m-%d %H:%M:%S") if s.last_run else "None"
        print(f"ID: {s.id} | Name: {s.name} | Status: {s.status} | Cron: {s.cron_expression} | Next: {next_run} | Last: {last_run}")
        
finally:
    db.close()
