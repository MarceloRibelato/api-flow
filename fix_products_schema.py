from sqlalchemy import create_engine, inspect, text
from app.config import settings

def inspect_schema():
    print(f"Connecting to: {settings.DATABASE_URL}")
    engine = create_engine(settings.DATABASE_URL)
    inspector = inspect(engine)
    
    if inspector.has_table("products"):
        columns = [col['name'] for col in inspector.get_columns("products")]
        print(f"Products table columns: {columns}")
        
        if 'channel_type' not in columns:
            print("MISSING COLUMN: channel_type")
            # Attempt to fix
            with engine.connect() as conn:
                try:
                    conn.execute(text("ALTER TABLE products ADD COLUMN channel_type VARCHAR(50) DEFAULT 'webhook'"))
                    conn.commit()
                    print("SUCCESS: Added channel_type column.")
                except Exception as e:
                    print(f"FAILED to add column: {e}")
        else:
            print("Column 'channel_type' already exists.")
    else:
        print("Table 'products' does not exist.")

if __name__ == "__main__":
    inspect_schema()
