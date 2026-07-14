import requests

url = "http://host.docker.internal:3000/api/chat/completions"
headers = {
    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6ImIyNjg4MWExLTYyYWEtNDM2ZS05ODBlLTU1ODMyY2ZkYzAzMyIsImV4cCI6MTc4NjI5MDk0MywianRpIjoiOTczNjczY2QtM2IyOC00NWM5LWExMDQtYTQzYzlmY2Q4NGNiIiwiaWF0IjoxNzgzODcxNzQzfQ.GsJxFXT4hXoQFTGH1qMNS-OU4gDCe4Lj866CADQscuk",
    "Content-Type": "application/json"
}
data = {
    "model": "Flow-IA",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.2
}

try:
    resp = requests.post(url, headers=headers, json=data, timeout=10)
    print("Status:", resp.status_code)
    print("Response:", resp.text)
except Exception as e:
    print("Exception:", e)
