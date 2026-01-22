from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.services.history_service import HistoryService
from app.schemas.history_schemas import ExecutionHistoryCreate, AssertionResult
from app.models.user_models import UserDB # Registers 'users' table


# Setup in-memory DB
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def run_debug():
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    try:
        print("Tables created.")
        
        user_id = 1
        assertions = [
            AssertionResult(
                source="status_code",
                operator="eq",
                target=200,
                actual=200,
                success=True
            )
        ]

        history_data = ExecutionHistoryCreate(
            method="GET",
            url="http://api.test/assertions",
            status_code=200,
            status_text="OK",
            response_time=600,
            assertions=assertions
        )

        print("Saving history...", history_data.assertions)
        saved = HistoryService.save(db, history_data, user_id)
        print(f"Saved ID: {saved.id}")
        print(f"Saved Assertions: {saved.assertions}")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    run_debug()
