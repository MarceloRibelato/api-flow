
from sqlalchemy import text, inspect
from app.database import engine

def migrate():
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns('products')]
    
    if 'notification_urls' not in columns:
        print("Adding notification_urls column to products table...")
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE products ADD COLUMN notification_urls TEXT"))
            conn.commit()
        print("✅ Column added successfully.")
    else:
        print("ℹ️ Column already exists.")

if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"❌ Migration failed: {e}")
