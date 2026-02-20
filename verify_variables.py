import os
import sys
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure 'app' is in path
sys.path.append(os.getcwd())

from app.database import Base
from app.services.flow_executor_service import FlowExecutorService
from app.models.variable_model import Variable
from app.models.feature_models import FeatureModel

print("🚀 Verification Start")

print("🛠️ Setting up DB...")
engine = create_engine("sqlite:///:memory:")
SessionLocal = sessionmaker(bind=engine)
print("🛠️ Creating metadata...")
Base.metadata.create_all(bind=engine)
print("🛠️ Creating session...")
db = SessionLocal()

print("🛠️ Seeding data...")
proj = FeatureModel(name="Test", product_id=1)
db.add(proj)
db.commit()
db.refresh(proj)

flow_meta = {'id': 1, 'project_id': proj.id, 'company_id': 1}
card_data = {
    'A': {
        'name': 'A',
        'apiCalls': [{
            'id': 'api1',
            'method': 'GET',
            'url': 'http://mock/auth',
            'extracts': [{'source': 'body', 'property': 'token', 'variable': 'TOKEN'}]
        }]
    },
    'B': {
        'name': 'B',
        'apiCalls': [{
            'id': 'api2',
            'method': 'GET',
            'url': 'http://mock/use/{{TOKEN}}'
        }]
    }
}
nodes = [{'id': 'A', 'position': {'x': 0, 'y': 0}}, {'id': 'B', 'position': {'x': 1, 'y': 0}}]
edges = [{'source': 'A', 'target': 'B'}]

# 3. Mocks
mock_load = patch('app.services.flow_service.FlowService.load').start()
mock_load.return_value = {'cardData': card_data, 'nodes': nodes, 'edges': edges}

mock_session_class = patch('requests.Session').start()
mock_session = mock_session_class.return_value

resp_a = MagicMock()
resp_a.status_code = 200
resp_a.json.return_value = {'token': 'val-123'}
resp_a.text = '{"token": "val-123"}'
resp_a.headers = {}

resp_b = MagicMock()
resp_b.status_code = 200
resp_b.json.return_value = {}
resp_b.text = '{}'
resp_b.headers = {}

mock_session.request.side_effect = [resp_a, resp_b]

# 4. Run
variables = {}
print("🏃 Running logic...")
s, f = FlowExecutorService.execute_flow_logic(db, flow_meta, proj.product_id, None, 1, variables)

# 5. Assert
print(f"Results: {s}/{f}")
print(f"Vars mapped: {variables}")

db_var = db.query(Variable).filter(Variable.name == 'TOKEN').first()
if db_var:
    print(f"DB Var: {db_var.name}={db_var.value}")
else:
    print("DB Var NOT found")

calls = mock_session.request.call_args_list
if len(calls) == 2:
    url_b = calls[1].args[1]
    print(f"Call B URL: {url_b}")
    if url_b == 'http://mock/use/val-123':
        print("✨ SUCCESS")
    else:
        print("❌ FAIL: Substitution failed")
else:
    print(f"❌ FAIL: Expected 2 calls, got {len(calls)}")

patch.stopall()
db.close()
