import pytest
from app.models.flow_models import FlowDB
from tests.test_flow import get_headers

def test_save_generated_flow_mobile_edge(client, db_session):
    headers = get_headers(client, "mobilesaveuser")
    prod = client.post(
        "/products/", headers=headers, json={"name": "Mobile Product"}
    ).json()
    
    feature = client.post(
        "/features/", headers=headers, json={"name": "Mobile Feature", "product_id": prod["id"]}
    ).json()

    # Create initial mobile flow
    initial_flow = {
        "projectId": feature["id"],
        "flow_type": "mobile",
        "nodes": [
            {"id": "start", "type": "startNode", "position": {"x": 0, "y": 0}, "data": {"name": "Start"}},
            {"id": "node_login", "type": "custom", "position": {"x": 100, "y": 0}, "data": {"name": "Login"}},
            {"id": "node_qtd", "type": "custom", "position": {"x": 200, "y": 0}, "data": {"name": "Informar Quantidade"}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "node_login", "type": "buttonedge"},
            {"id": "e2", "source": "node_login", "target": "node_qtd", "type": "buttonedge"}
        ],
        "cardData": {
            "node_login": {"name": "Login", "nodeType": "mobile", "e2eSteps": []},
            "node_qtd": {"name": "Informar Quantidade", "nodeType": "mobile", "e2eSteps": []}
        }
    }
    resp = client.post("/flow/save", headers=headers, json=initial_flow)
    assert resp.status_code == 200

    flow_obj = db_session.query(FlowDB).filter(FlowDB.project_id == feature["id"], FlowDB.flow_type == "mobile").first()
    assert flow_obj is not None
    flow_id = flow_obj.id

    # Now AI generates an alternative scenario with 1 node and edges with a dummy/start/different id
    gen_payload = {
        "name": "Abastecimento - Falha ao Continuar",
        "flow_type": "mobile",
        "nodes": [
            {"id": "ai_gen_1", "type": "custom", "data": {"name": "Falha ao Continuar"}}
        ],
        "edges": [
            {"id": "ai_e1", "source": "dummy_parent", "target": "ai_gen_1"}
        ],
        "cardData": {
            "ai_gen_1": {"name": "Falha ao Continuar", "nodeType": "mobile", "e2eSteps": [{"name": "Step 1"}]}
        }
    }

    save_resp = client.post(
        f"/analysis/skills/save-generated-flow?flow_id={flow_id}&parent_node_id=node_qtd",
        headers=headers,
        json=gen_payload
    )
    assert save_resp.status_code == 200

    # Now load the mobile flow
    loaded_resp = client.get(f"/flow/load/{feature['id']}?flow_type=mobile", headers=headers)
    assert loaded_resp.status_code == 200
    loaded_data = loaded_resp.json()

    print("LOADED NODES:", [n["id"] for n in loaded_data["nodes"]])
    print("LOADED EDGES:", [(e["source"], e["target"]) for e in loaded_data["edges"]])

    # Check if there is an edge whose source is node_qtd and target is the new node
    new_node = next((n for n in loaded_data["nodes"] if "Falha" in n["data"]["name"]), None)
    assert new_node is not None, "New node should exist in loaded nodes"

    connecting_edge = next((e for e in loaded_data["edges"] if e["source"] == "node_qtd" and e["target"] == new_node["id"]), None)
    assert connecting_edge is not None, f"Missing edge from node_qtd to {new_node['id']}! All edges: {loaded_data['edges']}"
    assert "target_node_id" in save_resp.json()
    assert save_resp.json()["target_node_id"] == new_node["id"]

def test_save_generated_flow_multiple_nodes_mobile(client, db_session):
    headers = get_headers(client, "multimobileuser")
    prod = client.post("/products/", headers=headers, json={"name": "Multi Product"}).json()
    feature = client.post("/features/", headers=headers, json={"name": "Multi Feature", "product_id": prod["id"]}).json()

    initial_flow = {
        "projectId": feature["id"],
        "flow_type": "mobile",
        "nodes": [
            {"id": "start", "type": "startNode", "position": {"x": 0, "y": 0}, "data": {"name": "Start"}},
            {"id": "node_parent", "type": "custom", "position": {"x": 100, "y": 0}, "data": {"name": "Parent Node"}}
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "node_parent", "type": "buttonedge"}
        ],
        "cardData": {
            "node_parent": {"name": "Parent Node", "nodeType": "mobile", "e2eSteps": []}
        }
    }
    client.post("/flow/save", headers=headers, json=initial_flow)
    flow_obj = db_session.query(FlowDB).filter(FlowDB.project_id == feature["id"], FlowDB.flow_type == "mobile").first()

    multi_payload = {
        "name": "Cenário 2 Passos",
        "flow_type": "mobile",
        "nodes": [
            {"id": "step_1", "type": "custom", "data": {"name": "Step 1"}},
            {"id": "step_2", "type": "custom", "data": {"name": "Step 2"}}
        ],
        "edges": [
            {"id": "e_internal", "source": "step_1", "target": "step_2"}
        ],
        "cardData": {
            "step_1": {"name": "Step 1", "nodeType": "mobile", "e2eSteps": []},
            "step_2": {"name": "Step 2", "nodeType": "mobile", "e2eSteps": []}
        }
    }

    save_resp = client.post(
        f"/analysis/skills/save-generated-flow?flow_id={flow_obj.id}&parent_node_id=node_parent",
        headers=headers,
        json=multi_payload
    )
    assert save_resp.status_code == 200
    res_data = save_resp.json()
    assert "target_node_id" in res_data
    assert len(res_data["new_node_ids"]) == 2

    loaded = client.get(f"/flow/load/{feature['id']}?flow_type=mobile", headers=headers).json()
    loaded_nodes = {n["id"]: n["data"]["name"] for n in loaded["nodes"]}
    # Verify both new nodes have distinct IDs
    assert len(loaded["nodes"]) == 4 # start + node_parent + step_1 + step_2

    s1_id = next(nid for nid, name in loaded_nodes.items() if name in ("Step 1", "Cenário 2 Passos"))
    s2_id = next(nid for nid, name in loaded_nodes.items() if name == "Step 2")
    assert s1_id != s2_id

    # Verify edge node_parent -> s1_id exists
    edge_p_s1 = next((e for e in loaded["edges"] if e["source"] == "node_parent" and e["target"] == s1_id), None)
    assert edge_p_s1 is not None, f"Missing edge node_parent -> step 1: {loaded['edges']}"

    # Verify internal edge s1_id -> s2_id exists
    edge_s1_s2 = next((e for e in loaded["edges"] if e["source"] == s1_id and e["target"] == s2_id), None)
    assert edge_s1_s2 is not None, f"Missing edge step 1 -> step 2: {loaded['edges']}"

def test_save_generated_flow_e2e_steps_normalization_and_inheritance(client, db_session):
    headers = get_headers(client, "e2enormuser")
    prod = client.post("/products/", headers=headers, json={"name": "E2E Norm Product"}).json()
    feature = client.post("/features/", headers=headers, json={"name": "E2E Norm Feature", "product_id": prod["id"]}).json()

    # Initial flow with a parent node that has full selectors
    initial_flow = {
        "projectId": feature["id"],
        "flow_type": "mobile",
        "nodes": [
            {"id": "start", "type": "startNode", "position": {"x": 0, "y": 0}, "data": {"name": "Start"}},
            {"id": "parent_node", "type": "custom", "position": {"x": 100, "y": 0}, "data": {"name": "Digitar Bomba"}}
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "parent_node", "type": "buttonedge"}
        ],
        "cardData": {
            "parent_node": {
                "name": "Digitar Bomba",
                "nodeType": "mobile",
                "e2eSteps": [
                    {
                        "id": "step_p1",
                        "type": "type",
                        "name": "Digitar Código",
                        "properties": {
                            "android_selector": "~input_bomba",
                            "ios_selector": "input_bomba_ios",
                            "selector": "~input_bomba",
                            "value": "1234"
                        }
                    }
                ]
            }
        }
    }
    client.post("/flow/save", headers=headers, json=initial_flow)
    flow_obj = db_session.query(FlowDB).filter(FlowDB.project_id == feature["id"], FlowDB.flow_type == "mobile").first()

    # AI generates an alternative node with generic type: 'action' and missing selector on step 1 (should inherit from parent)
    ai_payload = {
        "name": "Bomba Inexistente",
        "flow_type": "mobile",
        "nodes": [
            {"id": "ai_child", "type": "custom", "data": {"name": "Erro Bomba Inexistente"}}
        ],
        "edges": [
            {"id": "ai_e1", "source": "parent_node", "target": "ai_child"}
        ],
        "cardData": {
            "ai_child": {
                "name": "Erro Bomba Inexistente",
                "nodeType": "mobile",
                "e2eSteps": [
                    {
                        "id": "step_c1",
                        "type": "action",
                        "action": "type",
                        "name": "Passo 1",
                        "properties": {
                            "value": "9999"
                        }
                    },
                    {
                        "id": "step_c2",
                        "type": "action",
                        "action": "tap",
                        "name": "Passo 2",
                        "properties": {
                            "android_selector": "~btn_confirmar"
                        }
                    }
                ]
            }
        }
    }

    save_resp = client.post(
        f"/analysis/skills/save-generated-flow?flow_id={flow_obj.id}&parent_node_id=parent_node",
        headers=headers,
        json=ai_payload
    )
    assert save_resp.status_code == 200

    loaded = client.get(f"/flow/load/{feature['id']}?flow_type=mobile", headers=headers).json()
    new_node = next(n for n in loaded["nodes"] if "Bomba Inexistente" in n["data"]["name"] or "Erro" in n["data"]["name"])
    child_card = loaded["cardData"][new_node["id"]]
    steps = child_card["e2eSteps"]

    assert len(steps) == 2

    # Step 1: type was 'action' with action 'type', inherited selector from parent step_p1
    assert steps[0]["type"] == "type"
    assert steps[0]["properties"]["android_selector"] == "~input_bomba"
    assert steps[0]["properties"]["selector"] == "~input_bomba"
    assert steps[0]["properties"]["ios_selector"] == "input_bomba_ios"
    assert steps[0]["properties"]["value"] == "9999"

    # Step 2: type was 'action' with action 'tap', had its own android_selector
    assert steps[1]["type"] == "tap"
    assert steps[1]["properties"]["android_selector"] == "~btn_confirmar"
    assert steps[1]["properties"]["selector"] == "~btn_confirmar"
    assert "btn_confirmar" in steps[1]["name"]


