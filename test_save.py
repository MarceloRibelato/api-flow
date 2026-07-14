import sys, os
os.environ["DATABASE_URL"] = "postgresql://admin:admin@localhost:5432/flow_db"
os.environ["SECRET_KEY"] = "test"
os.environ["CELERY_BROKER_URL"] = "redis://localhost:6379/0"
os.environ["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/0"
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
import json

engine = create_engine(os.environ["DATABASE_URL"])
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

from app.routes.skill_routes import save_generated_flow

# Find the flow from earlier
with open('/home/server/Desktop/Projetos/Flow/flow-license-manager/backend/manager.db', 'rb') as f:
    pass # we can just use sqlite3 to get the last log

import sqlite3
conn = sqlite3.connect('/home/server/Desktop/Projetos/Flow/flow-license-manager/backend/manager.db')
cur = conn.cursor()
cur.execute('SELECT response_data FROM access_logs ORDER BY id DESC LIMIT 1;')
row = cur.fetchone()
data = json.loads(row[0])
alternatives = data['choices'][0]['message']['content']

# Save first alternative
print("Saving alt 1...")
try:
    save_generated_flow(alternatives[0], user_id=1, company_id=1, flow_id=31, db=db) # flow_id might not be 31, let's find a real flow_id
except Exception as e:
    print(f"Error 1: {e}")

