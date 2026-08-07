import urllib.request
import json
import urllib.parse
import sys

# 0. Register
try:
    register_req = urllib.request.Request("http://localhost:8000/api/auth/create", 
                                       data=json.dumps({"username": "testuser", "password": "TestPassword123!", "email": "test@test.com", "full_name": "Test User"}).encode('utf-8'),
                                       headers={'Content-Type': 'application/json'},
                                       method='POST')
    urllib.request.urlopen(register_req)
except urllib.error.HTTPError as e:
    if e.code != 409: # 409 is conflict, already exists
        print(e.read().decode())
        pass

# 1. Login to get token
try:
    req = urllib.request.Request("http://localhost:8000/api/auth/login", 
                                 data=json.dumps({'username': 'testuser', 'password': 'TestPassword123!'}).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'},
                                 method='POST')
    with urllib.request.urlopen(req) as response:
        token = json.loads(response.read().decode())['access_token']
except urllib.error.HTTPError as e:
    print(f"Login failed: {e.code} {e.read().decode()}")
    sys.exit(1)

# 2. Get history batches
req2 = urllib.request.Request("http://localhost:8000/api/history/batches?limit=10")
req2.add_header('Authorization', f'Bearer {token}')
with urllib.request.urlopen(req2) as response2:
    history = json.loads(response2.read().decode())

print("First few items in history batches:")
for item in history.get('data', [])[:5]:
    print(f"ID: {item.get('id')}, Execution_ID: {item.get('execution_id')}")
    print(f"API: {item.get('api_name')}, Status: {item.get('status_code')}")
    print(f"Error Message: {repr(item.get('error_message'))}")
    print(f"Success: {item.get('success')}")
    print(f"Assertions (count): {len(item.get('assertions') or [])}")
    print("-" * 40)
