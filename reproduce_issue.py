import urllib.request
import urllib.parse
import json
import sys

BASE_URL = "http://localhost:8000"

def ajax(url, method="GET", data=None):
    try:
        req = urllib.request.Request(url, method=method)
        req.add_header('Content-Type', 'application/json')
        
        if data:
            json_data = json.dumps(data).encode('utf-8')
            req.data = json_data
            
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.read().decode()}")
        # return e.code, e.read().decode() # Return error body if possible
        return e.code, None
    except Exception as e:
        print(f"Error: {e}")
        return 500, None

def test_persistence():
    PROJECT_ID = 1
    ENV_ID = 1

    # 1. GET Variables
    print("\n--- 1. Initial GET ---")
    status, vars = ajax(f"{BASE_URL}/variables?project_id={PROJECT_ID}&environment_id={ENV_ID}")
    
    if not vars:
        print("No variables found. Creating one...")
        new_var_payload = {
            "name": "TEST_VAR_PERSISTENCE",
            "value": "INITIAL_VALUE",
            "type": "static",
            "project_id": PROJECT_ID,
            "environment_id": ENV_ID
        }
        status, created_var = ajax(f"{BASE_URL}/variables", method="POST", data=new_var_payload)
        if status != 200:
            print("Failed to create variable.")
            return
        target_var = created_var
    else:
        target_var = vars[0]

    print(f"Target Variable: {target_var['name']} (ID: {target_var['id']}) = {target_var['value']}")

    # 2. PUT Variable (Update)
    print("\n--- 2. PUT Update ---")
    new_value = "UPDATED_VALUE_" + str(int(target_var['id']) + 999)
    update_payload = target_var.copy()
    update_payload['value'] = new_value
    
    # Cleaning payload
    if 'apiId' in update_payload: update_payload['api_id'] = update_payload.pop('apiId')
    if 'jsonPath' in update_payload: update_payload['json_path'] = update_payload.pop('jsonPath')
    if 'fakerType' in update_payload: update_payload['faker_type'] = update_payload.pop('fakerType')

    update_payload.pop('created_at', None)
    update_payload.pop('updated_at', None)
    
    if update_payload.get('api_id') == 'null' or update_payload.get('api_id') == '':
         update_payload['api_id'] = None

    status, updated_var_resp = ajax(f"{BASE_URL}/variables/{target_var['id']}", method="PUT", data=update_payload)
    
    if status != 200:
        print("Failed to update variable")
        return
    
    if not updated_var_resp:
         print("Update response empty?")
         return
         
    print(f"Update Response: {updated_var_resp['name']} = {updated_var_resp['value']}")

    # 3. GET Variables (Verify Persistence)
    print("\n--- 3. Verify GET ---")
    status, vars_after = ajax(f"{BASE_URL}/variables?project_id={PROJECT_ID}&environment_id={ENV_ID}")
    
    found = False
    for v in vars_after:
        if v['id'] == target_var['id']:
            found = True
            print(f"Variable Found: {v['name']} = {v['value']}")
            if v['value'] == new_value:
                print("SUCCESS: Value persisted.")
            else:
                print(f"FAILURE: Value mismatch. Expected {new_value}, got {v['value']}")
            break
            
    if not found:
        print("FAILURE: Variable not found after update.")

if __name__ == "__main__":
    test_persistence()
