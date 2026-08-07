from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.user_models import UserDB
db = SessionLocal()
batch_id = db.query(ApiExecutionHistory.batch_id).filter(ApiExecutionHistory.batch_id != None).first()[0]
company_id = db.query(UserDB.company_id).first()[0]
print("batch:", batch_id, "company:", company_id)
count = db.query(ApiExecutionHistory).filter(
    ApiExecutionHistory.batch_id == batch_id,
    ApiExecutionHistory.id.in_(
        db.query(ApiExecutionHistory.id).join(UserDB).filter(UserDB.company_id == company_id)
    )
).delete(synchronize_session=False)
print("count:", count)
db.rollback()
