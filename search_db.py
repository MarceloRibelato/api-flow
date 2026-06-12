import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Import from app.config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.config import settings

DATABASE_URL = settings.DATABASE_URL
print(f"Connecting to database: {DATABASE_URL}")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

try:
    # Get all tables
    result = db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
    tables = [row[0] for row in result.fetchall()]
    print(f"Tables found: {tables}")
    
    for table in tables:
        # Get columns
        columns_res = db.execute(text(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='{table}'"))
        columns = columns_res.fetchall()
        for col_name, col_type in columns:
            if col_type in ('character varying', 'text', 'json', 'jsonb'):
                # Search for Gherkin or BDD
                query = text(f"SELECT id, \"{col_name}\" FROM \"{table}\" WHERE CAST(\"{col_name}\" AS TEXT) ILIKE :term")
                matches = db.execute(query, {"term": "%Gherkin%"}).fetchall()
                if matches:
                    print(f"Match found in Table '{table}', Column '{col_name}':")
                    for match in matches:
                        print(f"  ID: {match[0]}, Content snippet: {str(match[1])[:200]}")
finally:
    db.close()
