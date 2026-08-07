import os
import sys

sys.path.insert(0, os.path.abspath("."))
from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
results = db.execute(text("SELECT method, count(*) FROM api_execution_history GROUP BY method")).fetchall()
for row in results:
    print(row)
