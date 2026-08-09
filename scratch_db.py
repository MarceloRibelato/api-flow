import requests
try:
    res = requests.get("http://localhost:8000/history/")
    print("STATUS:", res.status_code)
    if res.status_code == 200:
        for i in res.json().get('items', [])[:10]:
            print(f"ID: {i.get('id')} NAME: {i.get('api_name')} METHOD: {i.get('method')} ERR: {i.get('error_message')}")
            print(f"  BODY: {str(i.get('response_body'))[:50]}")
except Exception as e:
    print(e)
