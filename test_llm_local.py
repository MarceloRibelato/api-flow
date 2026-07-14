import requests

# Test both /api/chat/completions and /api/v1/chat/completions
headers = {
    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6ImIyNjg4MWExLTYyYWEtNDM2ZS05ODBlLTU1ODMyY2ZkYzAzMyIsImV4cCI6MTc4NjI5MDk0MywianRpIjoiOTczNjczY2QtM2IyOC00NWM5LWExMDQtYTQzYzlmY2Q4NGNiIiwiaWF0IjoxNzgzODcxNzQzfQ.GsJxFXT4hXoQFTGH1qMNS-OU4gDCe4Lj866CADQscuk",
    "Content-Type": "application/json"
}
data = {
    "model": "Flow-IA",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.2
}

print("Testing /api/v1/...")
try:
    resp = requests.post("http://localhost:3000/api/v1/chat/completions", headers=headers, json=data, timeout=5)
    print("V1 Status:", resp.status_code, resp.text)
except Exception as e:
    print("V1 Error:", e)

print("Testing /api/...")
try:
    resp = requests.post("http://localhost:3000/api/chat/completions", headers=headers, json=data, timeout=5)
    print("API Status:", resp.status_code, resp.text)
except Exception as e:
    print("API Error:", e)

