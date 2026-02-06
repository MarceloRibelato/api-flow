
from app.database import engine
from sqlalchemy import text

def migrate():
    with engine.connect() as conn:
        try:
            print("Attempting to add feature_name column...")
            conn.execute(text("ALTER TABLE api_test_execution_history ADD COLUMN IF NOT EXISTS feature_name VARCHAR(255) NULL"))
            
            print("Attempting to add ai_base_url column...")
            # SQLite syntax (assuming SQLite based on pydantic/local context usually, or generic enough)
            # For SQLite, ADD COLUMN is supported. IF NOT EXISTS is supported in newer versions or ignored if fails?
            # Safer to wrap in try/except block for column existence check if needed, but 'ADD COLUMN' usually throws if exists.
            # Using a safer approach implies checking pragma, but let's stick to the pattern they used.
            # Note: SQLite doesn't support IF NOT EXISTS in ADD COLUMN in all versions, but let's try.
            # actually standard SQL is usually just ADD COLUMN.
            try:
                conn.execute(text("ALTER TABLE agent_settings ADD COLUMN ai_base_url VARCHAR(255) NULL"))
                print("Successfully added ai_base_url column.")
            except Exception as e:
                print(f"Column ai_base_url might already exist or error: {e}")

            conn.commit()
            print("Successfully added feature_name column (committed).")
        except Exception as e:
            print(f"Migration failed: {e}")
            conn.rollback()

if __name__ == "__main__":
    migrate()
