import sys
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append("app")
from app.models.history_models import FrontRecordingDB
from core.database import SQLALCHEMY_DATABASE_URL

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

recording = db.query(FrontRecordingDB).order_by(FrontRecordingDB.id.desc()).first()
if recording:
    print(f"Recording ID: {recording.id}")
    print(f"Name: {recording.name}")
    print(f"Requests Count: {len(recording.requests) if recording.requests else 0}")
    print("Requests:")
    for r in (recording.requests or []):
        print(f" - {r.get('method')} {r.get('url')} | Node: {r.get('customNodeName')} | Title: {r.get('pageTitle')}")
else:
    print("No recordings found.")

db.close()
