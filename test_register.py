import requests
import json

url = "http://localhost:8000/auth/create"
payload = {
    "username": "bruna.ribelato@gmail.com",
    "email": "bruna.ribelato@gmail.com",
    "full_name": "Bruna Ribelato",
    "cpf": "321.432.432-42",
    "company": "Veloe GO",
    "cnpj": "24.234.324/3243-24",
    "phone": "(19) 98962-4096",
    "password": "test123"
}

try:
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'response'):
        print(f"Response Text: {e.response.text}")
