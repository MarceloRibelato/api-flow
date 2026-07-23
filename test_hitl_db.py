from app.database import SessionLocal
from app.models.hitl_models import HitlSessionDB

db = SessionLocal()
sessions = db.query(HitlSessionDB).all()
print(f"Total sessions: {len(sessions)}")
for s in sessions:
    print(f"Session {s.id}: status={s.status}, exec_id={s.execution_id}, node_id={s.node_id}")
db.close()
