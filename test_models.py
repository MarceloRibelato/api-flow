import requests
import json

headers = {
    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6ImIyNjg4MWExLTYyYWEtNDM2ZS05ODBlLTU1ODMyY2ZkYzAzMyIsImV4cCI6MTc4NjI5MDk0MywianRpIjoiOTczNjczY2QtM2IyOC00NWM5LWExMDQtYTQzYzlmY2Q4NGNiIiwiaWF0IjoxNzgzODcxNzQzfQ.GsJxFXT4hXoQFTGH1qMNS-OU4gDCe4Lj866CADQscuk"
}

try:
    resp = requests.get("http://localhost:3000/api/models", headers=headers, timeout=5)
    models = resp.json().get("data", [])
    for m in models:
        print(f"ID: {m.get('id')}, Name: {m.get('name')}")
except Exception as e:
    print("Error:", e)

