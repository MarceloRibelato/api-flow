import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session
from app.services.flow_executor_service import FlowExecutorService
from app.models.variable_model import Variable

def test_hybrid_parallel_execution_grouping(db_session: Session):
    # 1. Setup Project
    from app.models.product_models import ProductModel
    from app.models.feature_models import FeatureModel
    
    product = ProductModel(name="Test Product", company_id=1)
    db_session.add(product)
    db_session.flush()
    
    proj = FeatureModel(name="Hybrid Parallel Flow", product_id=product.id)
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    # 2. Setup Flow Data (1 Node with 4 steps)
    # API 1: Sequential, extracts 'VAR1'
    # API 2: Parallel, uses '{{VAR1}}'
    # API 3: Parallel, extracts 'VAR3'
    # API 4: Sequential, uses '{{VAR3}}'
    
    flow_meta = {
        'id': 1,
        'name': 'Hybrid Flow',
        'project_id': proj.id,
        'company_id': 1
    }
    
    card_data = {
        'node1': {
            'name': 'Hybrid Node',
            # NO parallelExecution global here, but we'll simulate it in the card object
            'parallelExecution': True, 
            'apiCalls': [
                {
                    'id': 'api1',
                    'name': 'API 1 (Seq)',
                    'method': 'GET',
                    'url': 'http://mock.com/1',
                    'parallel': False,
                    'extracts': [{'source': 'body', 'property': 'val', 'variable': 'VAR1'}]
                },
                {
                    'id': 'api2',
                    'name': 'API 2 (Par)',
                    'method': 'GET',
                    'url': 'http://mock.com/2/{{VAR1}}',
                    'parallel': True,
                    'extracts': [{'source': 'body', 'property': 'val', 'variable': 'VAR2'}]
                },
                {
                    'id': 'api3',
                    'name': 'API 3 (Par)',
                    'method': 'GET',
                    'url': 'http://mock.com/3',
                    'parallel': True,
                    'extracts': [{'source': 'body', 'property': 'val', 'variable': 'VAR3'}]
                },
                {
                    'id': 'api4',
                    'name': 'API 4 (Seq)',
                    'method': 'GET',
                    'url': 'http://mock.com/4/{{VAR2}}/{{VAR3}}',
                    'parallel': False
                }
            ]
        }
    }
    
    nodes = [{'id': 'node1', 'type': 'custom', 'position': {'x': 0, 'y': 0}, 'data': {'name': 'Node 1'}}]
    edges = []
    
    # Mock FlowService.load
    with patch('app.services.flow_service.FlowService.load') as mock_load:
        mock_load.return_value = {
            'cardData': card_data,
            'nodes': nodes,
            'edges': edges
        }
        
        # Mock requests.Session
        with patch('requests.Session') as mock_session_class:
            mock_session = mock_session_class.return_value
            
            # API 1 response
            res1 = MagicMock()
            res1.status_code = 200
            res1.json.return_value = {'val': 'v1'}
            res1.text = '{"val": "v1"}'
            res1.headers = {}

            # API 2 response
            res2 = MagicMock()
            res2.status_code = 200
            res2.json.return_value = {'val': 'v2'}
            res2.text = '{"val": "v2"}'
            res2.headers = {}

            # API 3 response
            res3 = MagicMock()
            res3.status_code = 200
            res3.json.return_value = {'val': 'v3'}
            res3.text = '{"val": "v3"}'
            res3.headers = {}

            # API 4 response
            res4 = MagicMock()
            res4.status_code = 200
            res4.json.return_value = {'ok': True}
            res4.text = '{"ok": true}'
            res4.headers = {}

            def mock_request(method, url, **kwargs):
                if url == 'http://mock.com/1': return res1
                if 'http://mock.com/2' in url: return res2
                if url == 'http://mock.com/3': return res3
                if 'http://mock.com/4' in url: return res4
                return MagicMock(status_code=404)

            mock_session.request.side_effect = mock_request
            
            variables = {}
            
            # Mock VariableService.create to avoid DB thread issues
            with patch('app.services.variable_service.VariableService.create') as mock_var_save:
                
                # Execute
                success, fail = FlowExecutorService.execute_flow_logic(
                    db_session, flow_meta, proj.product_id, None, 1, variables
                )
                
                assert success == 4
                assert fail == 0
            
            # Verify variable propagation
            assert variables.get('VAR1') == 'v1'
            assert variables.get('VAR2') == 'v2'
            assert variables.get('VAR3') == 'v3'
            
            calls = mock_session.request.call_args_list
            assert len(calls) == 4
            
            urls_called = [call.args[1] for call in calls]
            
            # API 1 should be first
            assert urls_called[0] == 'http://mock.com/1'
            
            # API 2 and 3 can be in any order, but must exist
            assert 'http://mock.com/2/v1' in urls_called[1:3]
            assert 'http://mock.com/3' in urls_called[1:3]
            
            # API 4 should be last
            assert urls_called[3] == 'http://mock.com/4/v2/v3'
            
            print("\n✅ Hybrid Parallel Execution Test Passed!")
