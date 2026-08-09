import requests

try:
    # Try to fetch the latest execution history
    r = requests.post("http://localhost:8000/api/v1/execute/history", json={"limit": 5})
    if r.status_code == 404:
        r = requests.post("http://localhost:8000/api/v1/history", json={"limit": 5})
    print(r.status_code)
    print(r.json())
except Exception as e:
    print(e)
