
from app.database import engine, Base
from sqlalchemy import text

def migrate():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE api_test_execution_history ADD COLUMN feature_name VARCHAR(255) NULL"))
            print("Successfully added feature_name column.")
        except Exception as e:
            print(f"Migration failed (Column might exist): {e}")

if __name__ == "__main__":
    migrate()
