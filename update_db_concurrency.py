
from sqlalchemy import create_engine, text
from app.config import settings
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_max_concurrency_column():
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        try:
            # Check if column exists
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='schedules' AND column_name='max_concurrency'"))
            if result.fetchone():
                logger.info("Column 'max_concurrency' already exists in 'schedules'. Skipping.")
                return

            # Add column
            logger.info("Adding 'max_concurrency' column to 'schedules' table...")
            conn.execute(text("ALTER TABLE schedules ADD COLUMN max_concurrency INTEGER NULL"))
            conn.commit()
            logger.info("Column added successfully.")
            
        except Exception as e:
            logger.error(f"Error adding column: {e}")
            raise

if __name__ == "__main__":
    add_max_concurrency_column()
