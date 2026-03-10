import requests
import json

BASE_URL = "http://127.0.0.1:8000"

def test_password_reset_flow():
    email = "admin@example.com" # Adjust to a real user in your DB
    
    print(f"1. Requesting password reset for {email}...")
    resp = requests.post(f"{BASE_URL}/auth/forgot-password", json={"email": email})
    print(f"Response: {resp.status_code} - {resp.json()}")
    
    print("\n2. Testing reset with invalid token...")
    resp = requests.post(f"{BASE_URL}/auth/reset-password", json={
        "token": "invalid-token",
        "new_password": "NewPassword123!"
    })
    print(f"Response (Expected 400): {resp.status_code} - {resp.json()}")

if __name__ == "__main__":
    try:
        test_password_reset_flow()
    except Exception as e:
        print(f"Error: {e}")
