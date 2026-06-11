def get_headers(client, username="inventoryuser"):
    client.post("/auth/create", json={
        "username": username, 
        "password": "Password123!",
        "accepted_terms": True
    })
    token = client.post(
        "/auth/login", json={"username": username, "password": "Password123!"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_get_cards_inventory(client):
    headers = get_headers(client)
    
    # 1. Create a product and feature
    prod = client.post(
        "/products/", headers=headers, json={"name": "Inv Product"}
    ).json()
    feature = client.post(
        "/features/", headers=headers, json={"name": "Inv Feature", "product_id": prod["id"]}
    ).json()
    
    # 2. Save a flow with a card
    flow_data = {
        "projectId": feature["id"],
        "flow_type": "api",
        "nodes": [{"id": "n1", "type": "custom", "position": {"x": 0, "y": 0}, "data": {"name": "Card 1"}}],
        "edges": [],
        "cardData": {"n1": {"name": "Card 1", "description": "Desc 1", "color": "#ff0000"}},
    }
    client.post("/flow/save", headers=headers, json=flow_data)
    
    # 3. Fetch inventory
    response = client.get("/flow/cards/inventory", headers=headers)
    assert response.status_code == 200
    res_data = response.json()
    assert "cards" in res_data
    inventory = res_data["cards"]
    
    # 4. Verify card is in inventory
    assert len(inventory) >= 1
    card1 = next((c for c in inventory if c["name"] == "Card 1"), None)
    assert card1 is not None
    assert card1["description"] == "Desc 1"
    assert card1["color"] == "#ff0000"
    assert "sourceFlow" in card1
