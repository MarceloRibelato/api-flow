import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session
from app.services.flow_executor_service import FlowExecutorService
from app.models.variable_model import Variable
from app.schemas.variable_schemas import VariableCreate

def test_variable_extraction_and_usage_cycle(db_session: Session):
    # 1. Setup Project
    from app.models.feature_models import FeatureModel
    from app.models.product_models import ProductModel
    
    product = ProductModel(name="Test Variable Product", company_id=1)
    db_session.add(product)
    db_session.flush()
    
    proj = FeatureModel(name="Test Variable Flow", product_id=product.id)
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    # 2. Setup Flow Data (2 Nodes)
    # Node A: Extracts 'token' from response body
    # Node B: Uses '{{TOKEN}}' in URL
    flow_meta = {
        'id': 1,
        'name': 'Extraction Flow',
        'project_id': proj.id,
        'company_id': 1
    }
    
    card_data = {
        'nodeA': {
            'name': 'Node A',
            'apiCalls': [{
                'id': 'api1',
                'name': 'Get Token',
                'method': 'GET',
                'url': 'http://mock-api.com/auth',
                'extracts': [{'source': 'body', 'property': 'token', 'variable': 'TOKEN'}]
            }]
        },
        'nodeB': {
            'name': 'Node B',
            'apiCalls': [{
                'id': 'api2',
                'name': 'Use Token',
                'method': 'GET',
                'url': 'http://mock-api.com/secure/{{TOKEN}}',
                'assertions': []
            }]
        }
    }
    
    nodes = [
        {'id': 'nodeA', 'position': {'x': 0, 'y': 0}},
        {'id': 'nodeB', 'position': {'x': 200, 'y': 0}}
    ]
    edges = [
        {'source': 'nodeA', 'target': 'nodeB'}
    ]
    
    # Mock FlowService.load to return our test flow
    with patch('app.services.flow_service.FlowService.load') as mock_load:
        mock_load.return_value = {
            'cardData': card_data,
            'nodes': nodes,
            'edges': edges
        }
        
        # Mock requests.Session
        with patch('requests.Session') as mock_session_class:
            mock_session = mock_session_class.return_value
            
            # Response for Node A
            resp_a = MagicMock()
            resp_a.status_code = 200
            resp_a.json.return_value = {'token': 'secret-123'}
            resp_a.text = '{"token": "secret-123"}'
            resp_a.headers = {}
            
            # Response for Node B
            resp_b = MagicMock()
            resp_b.status_code = 200
            resp_b.json.return_value = {'status': 'ok'}
            resp_b.text = '{"status": "ok"}'
            resp_b.headers = {}
            
            # Sequence responses
            mock_session.request.side_effect = [resp_a, resp_b]
            
            # Initial variables
            variables = {}
            
            # 3. Execute Flow
            success, fail = FlowExecutorService.execute_flow_logic(
                db_session, flow_meta, proj.product_id, None, 1, variables
            )
            
            # 4. Assertions
            assert success == 2
            assert fail == 0
            
            # Check if variable was saved to variables_dict
            assert 'TOKEN' in variables
            assert variables['TOKEN'] == 'secret-123'
            
            # Check if variable was saved to DB
            db_var = db_session.query(Variable).filter(Variable.name == 'TOKEN').first()
            assert db_var is not None
            assert db_var.value == 'secret-123'
            
            # Check if second request used the variable
            # First call was to /auth
            # Second call should be to /secure/secret-123
            calls = mock_session.request.call_args_list
            assert len(calls) == 2
            
            # Verify Second Call URL
            node_b_call_args = calls[1]
            assert node_b_call_args.args[1] == 'http://mock-api.com/secure/secret-123'
            
            print("\n✅ Verification Successful: Variable extracted and reused correctly!")
