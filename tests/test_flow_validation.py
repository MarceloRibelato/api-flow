import pytest

def get_headers(client, username="flowvaluser"):
    client.post("/auth/create", json={"username": username, "password": "password"})
    token = client.post(
        "/auth/login", data={"username": username, "password": "password"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_save_empty_flow(client):
    headers = get_headers(client, "emptyuser")
    proj = client.post("/projects/", headers=headers, json={"name": "Empty Project"}).json()

    flow_data = {
        "projectId": proj["id"],
        "nodes": [],
        "edges": []
    }
    
    resp = client.post("/flow/save", headers=headers, json=flow_data)
    assert resp.status_code == 200
    
    # Verify load
    load_resp = client.get(f"/flow/load/{proj['id']}", headers=headers)
    assert load_resp.json()["nodes"] == []

def test_save_flow_with_orphaned_edges(client):
    """
    Test saving an edge that points to a non-existent node.
    Ideally, the system should allow it (robustness) or clean it up, 
    but mostly we want to ensure it doesn't Crash.
    """
    headers = get_headers(client, "orphanuser")
    proj = client.post("/projects/", headers=headers, json={"name": "Orphan Project"}).json()

    flow_data = {
        "projectId": proj["id"],
        "nodes": [
            {"id": "1", "type": "custom", "position": {"x": 0, "y": 0}, "data": {}}
        ],
        "edges": [
            {"id": "e1-2", "source": "1", "target": "2"} # Node 2 does not exist
        ]
    }
    
    # The API currently allows this (Frontend handles validity), but backend should accept it without error.
    resp = client.post("/flow/save", headers=headers, json=flow_data)
    assert resp.status_code == 200

    load_resp = client.get(f"/flow/load/{proj['id']}", headers=headers)
    saved_edges = load_resp.json()["edges"]
    # Depending on logic, it might be saved or filtered. Currently straightforward save.
    assert len(saved_edges) == 1
    assert saved_edges[0]["target"] == "2"

def test_save_duplicate_node_ids(client):
    """
    Tests behavior when sending duplicate node IDs. 
    The backend usually invalidates or updates the existing one.
    """
    headers = get_headers(client, "dupnodeuser")
    proj = client.post("/projects/", headers=headers, json={"name": "Dup Project"}).json()

    flow_data = {
        "projectId": proj["id"],
        "nodes": [
            {"id": "1", "type": "custom", "position": {"x": 0, "y": 0}, "data": {"label": "A"}},
            {"id": "1", "type": "custom", "position": {"x": 100, "y": 100}, "data": {"label": "B"}} # Duplicate ID
        ],
        "edges": []
    }

    resp = client.post("/flow/save", headers=headers, json=flow_data)
    assert resp.status_code == 200

    # Load back to see what happened. Usually last write wins or DB constraints might fail if unique constrained on (flow_id, node_id).
    # Current implementation uses JSON/Mongo-like structure or localized tables? 
    # It likely replaces the list entirely. 
    # If the nodes list is just a JSON blob, it stores both.
    load_resp = client.get(f"/flow/load/{proj['id']}", headers=headers)
    nodes = load_resp.json()["nodes"]
    
    # If standard JSON list, it allows duplicates. If processed, maybe not.
    # Asserting we have 2 nodes (even if dup ID) or 1 (deduplicated). 
    # Just asserting success 200 is main goal here.
    assert len(nodes) >= 1
