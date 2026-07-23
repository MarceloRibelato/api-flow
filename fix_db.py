from sqlalchemy import create_engine, text
from app.core.config import settings

engine = create_engine(settings.DATABASE_URL)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE flow_card_data ADD COLUMN message_queues JSON DEFAULT '[]'::json;"))
        conn.commit()
        print("Column added successfully!")
    except Exception as e:
        print(f"Error adding column (maybe it already exists?): {e}")
