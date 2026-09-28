from sqlalchemy import create_engine, text
engine = create_engine("postgresql://postgres:postgres@localhost:5432/postgres")
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE service_mocks ADD COLUMN force_real_api BOOLEAN DEFAULT FALSE NOT NULL;"))
        conn.commit()
        print("Column force_real_api added successfully!")
    except Exception as e:
        print("Error:", e)
