import sys
import os

# Ensure we can import from app
sys.path.append(os.getcwd())

from sqlalchemy import text
from app.database import engine

def migrate_schedules():
    print("Starting migration check for 'schedules' table...")
    with engine.connect() as connection:
        trans = connection.begin()
        try:
            # Check if column exists (PostgreSQL compatible)
            check_sql = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='schedules' AND column_name='notifications_enabled';
            """)
            result = connection.execute(check_sql)
            if not result.fetchone():
                print("Adding 'notifications_enabled' column to 'schedules' table...")
                # Add column with default TRUE (as per requirement)
                alter_sql = text("ALTER TABLE schedules ADD COLUMN notifications_enabled BOOLEAN DEFAULT TRUE")
                connection.execute(alter_sql)
                print("Column 'notifications_enabled' added successfully.")
            else:
                print("'notifications_enabled' column already exists.")
            
            trans.commit()
            print("Migration completed successfully.")
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate_schedules()
