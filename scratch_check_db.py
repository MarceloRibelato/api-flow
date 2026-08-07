import os
import sys

# Setup environment to load the app correctly
sys.path.insert(0, os.path.abspath("."))
from app.database import SessionLocal
from app.models.api_test_history_models import ApiExecutionHistory

db = SessionLocal()
histories = db.query(ApiExecutionHistory).order_by(ApiExecutionHistory.id.desc()).limit(15).all()

for h in histories:
    print(f"ID: {h.id}, Type: {h.execution_type}, Method: {h.method}, API Name: {h.api_name}, Node Name: {h.node_name}, Body: {h.response_body[:50] if h.response_body else ''}")
