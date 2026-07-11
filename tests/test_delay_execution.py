import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session
from app.services.flow_executor_service import FlowExecutorService
import time

def test_delay_execution_parsing(db_session: Session):
    # Setup dummy project data
    from app.models.product_models import ProductModel
    from app.models.feature_models import FeatureModel
    
    product = ProductModel(name="Delay Test Product", company_id=1)
    db_session.add(product)
    db_session.flush()
    
    proj = FeatureModel(name="Delay Flow", product_id=product.id)
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    flow_meta = {
        'id': 1,
        'name': 'Delay Test Flow',
        'project_id': proj.id,
        'company_id': 1
    }
    
    # API 1: Uses integer delay properly
    # API 2: Uses string number delay (should cast safely to int)
    # API 3: Uses invalid string for delay (should fallback to 0)
    card_data = {
        'node1': {
            'apiCalls': [
                {
                    'id': 'api1',
                    'name': 'Delay 1 (Int)',
                    'method': 'GET',
                    'url': 'http://mock.com/1',
                    'delay': 100, # 100ms
                    'parallel': False
                },
                {
                    'id': 'api2',
                    'name': 'Delay 2 (Str)',
                    'method': 'GET',
                    'url': 'http://mock.com/2',
                    'delay': '50', # 50ms
                    'parallel': False
                },
                {
                    'id': 'api3',
                    'name': 'Delay 3 (Invalid)',
                    'method': 'GET',
                    'url': 'http://mock.com/3',
                    'delay': 'XYZ', # should not crash
                    'parallel': False
                }
            ]
        }
    }
    
    nodes = [{'id': 'node1', 'type': 'custom', 'position': {'x': 0, 'y': 0}, 'data': {'name': 'Node 1'}}]
    edges = []
    
    with patch('app.services.flow_service.FlowService.load') as mock_load:
        mock_load.return_value = {
            'cardData': card_data,
            'nodes': nodes,
            'edges': edges
        }
        
        with patch('requests.Session') as mock_session_class:
            mock_session = mock_session_class.return_value
            
            res = MagicMock()
            res.status_code = 200
            res.json.return_value = {'val': 'ok'}
            res.text = '{"val": "ok"}'
            res.headers = {}
            mock_session.request.return_value = res
            
            # Patch time.sleep to intercept the delay calculation without actually sleeping in tests
            with patch('time.sleep') as mock_sleep:
                variables = {}
                
                success, fail = FlowExecutorService.execute_flow_logic(
                    db_session, flow_meta, proj.product_id, None, 1, variables
                )
                
                assert success == 3, "All 3 APIs should be successful"
                assert fail == 0
                
                # Check sleep calls
                calls = mock_sleep.call_args_list
                assert len(calls) == 2, "Should have slept exactly twice (API 3 fallback to 0 doesn't trigger sleep)"
                
                # First sleep should be 100ms (0.1s)
                assert calls[0].args[0] == 0.1
                # Second sleep should be 50ms (0.05s)
                assert calls[1].args[0] == 0.05
                
                print("\n✅ Delay Execution parsing and fallback test passed!")
