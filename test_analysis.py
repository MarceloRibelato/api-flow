import json

original_flow_data = {
    "nodes": [{"id": "original_1"}],
    "edges": [],
    "cardData": {}
}

suggested_data = {
    "nodes": [{"id": "suggested_1"}],
    "edges": [],
    "cardData": {}
}

merged_nodes = original_flow_data.get("nodes", []) + suggested_data.get("nodes", [])
print("Merged Nodes:", merged_nodes)
