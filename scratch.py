import os
import requests
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.flow_models import FlowDB
from app.services.flow_service import FlowService
from app.schemas.flow_schemas import FlowSaveSchema

# Connect to DB directly for testing
engine = create_engine("postgresql://postgres:postgres@localhost:5432/flow_db")
Session = sessionmaker(bind=engine)
db = Session()

# Check if there is an existing flow
flow = db.query(FlowDB).first()
if flow:
    project_id = flow.project_id
    company_id = flow.company_id
else:
    project_id = 1
    company_id = 1

print(f"Testing with project_id={project_id}, company_id={company_id}")

payload = {
    "projectId": project_id,
    "flowId": flow.id if flow else None,
    "flow_type": "e2e",
    "nodes": [
        {
            "id": "node_test_123",
            "type": "custom",
            "position": {"x": 200, "y": 200},
            "data": {
                "name": "Test Node",
                "color": "#3b82f6",
                "description": "",
                "projectId": project_id,
                "isCollapsed": True
            }
        }
    ],
    "edges": [],
    "cardData": {
        "node_test_123": {
            "name": "Test Node",
            "color": "#3b82f6",
            "description": "Test Desc",
            "bddScenarios": [],
            "apiCalls": [],
            "e2eSteps": [{"id": "1", "type": "action", "name": "test step"}],
            "dbQueries": [],
            "messageQueues": []
        }
    }
}

try:
    schema = FlowSaveSchema(**payload)
    print("Pydantic validation passed!")
    result = FlowService.save(db, schema, company_id=company_id, user_id=1)
    print("Save result:", result)
    
    # Load back
    loaded = FlowService.load(db, project_id, company_id, flow_type="e2e")
    print("Loaded nodes:", len(loaded['nodes']))
    print("Loaded cardData:", loaded['cardData'].keys())
except Exception as e:
    print("Error:", e)
