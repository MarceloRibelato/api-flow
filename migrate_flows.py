import sys
import os

# Add parent directory to path to import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.database import engine

def migrate():
    print("Checking for migration...")
    with engine.connect() as conn:
        # Check if 'name' column exists in flow_data
        try:
            # SQLite specific pragmas or just try to select
            result = conn.execute(text("SELECT name FROM flow_data LIMIT 1"))
            print("Column 'name' already exists.")
        except Exception:
            print("Column 'name' missing. Adding columns...")
            try:
                # Add Name
                conn.execute(text("ALTER TABLE flow_data ADD COLUMN name VARCHAR(255) DEFAULT 'Fluxo Principal'"))
                print("Added 'name'.")
                
                # Add Created At
                conn.execute(text("ALTER TABLE flow_data ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"))
                print("Added 'created_at'.")

                # Add Updated At
                conn.execute(text("ALTER TABLE flow_data ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"))
                print("Added 'updated_at'.")
                
                conn.commit()
                print("Migration successful.")
            except Exception as e:
                print(f"Migration failed: {e}")

if __name__ == "__main__":
    migrate()
