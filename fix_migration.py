
from app.database import engine
from sqlalchemy import text

def migrate():
    with engine.connect() as conn:
        try:
            print("Attempting to add feature_name column...")
            conn.execute(text("ALTER TABLE api_test_execution_history ADD COLUMN IF NOT EXISTS feature_name VARCHAR(255) NULL"))
            conn.commit()
            print("Successfully added feature_name column (committed).")
        except Exception as e:
            print(f"Migration failed: {e}")
            conn.rollback()

if __name__ == "__main__":
    migrate()
