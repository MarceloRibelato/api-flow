import pytest
import json
import io

def test_import_curl(client):
    curl_input = 'curl -X POST "https://api.test/v1/update" -H "Authorization: Bearer test" -d "{\\"id\\": 1}"'
    response = client.post("/import/curl", json={"curl": curl_input})
    
    assert response.status_code == 200
    data = response.json()
    assert data["method"] == "POST"
    assert data["url"] == "https://api.test/v1/update"
    assert any(h["key"] == "Authorization" and h["value"] == "Bearer test" for h in data["headers"])
    assert data["body"] == '{"id": 1}'

def test_import_postman(client):
    postman_collection = {
        "info": {"name": "Test Collection"},
        "item": [
            {
                "name": "Get User",
                "request": {
                    "method": "GET",
                    "url": {"raw": "https://api.test/user/1"},
                    "header": [{"key": "X-Api-Key", "value": "secret"}]
                }
            },
            {
                "name": "Create User",
                "request": {
                    "method": "POST",
                    "url": "https://api.test/user", # String URL format
                    "body": {
                        "mode": "raw",
                        "raw": '{"name": "John"}'
                    }
                }
            }
        ]
    }
    
    file_content = json.dumps(postman_collection).encode('utf-8')
    files = {'file': ('collection.json', io.BytesIO(file_content), 'application/json')}
    
    response = client.post("/import/postman", files=files)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Get User"
    assert data[0]["url"] == "https://api.test/user/1"
    assert data[1]["method"] == "POST"
    assert data[1]["body"] == '{"name": "John"}'

def test_import_postman_invalid_file(client):
    files = {'file': ('not_json.txt', io.BytesIO(b'hello'), 'text/plain')}
    response = client.post("/import/postman", files=files)
    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]
