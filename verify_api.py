import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def test_execution():
    print("1. Triggering Execution...")
    try:
        payload = {
            "product_id": 1,
            "feature_id": "all",
            "environment_id": 1,
            "name": "Verification Run via Script"
        }
        resp = requests.post(f"{BASE_URL}/execute/create", json=payload)
        resp.raise_for_status()
        data = resp.json()
        print(f"   SUCCESS: {data}")
        schedule_id = data['schedule_id']
    except Exception as e:
        print(f"   FAILED: {e}")
        # Print response body if available
        if 'resp' in locals(): print(resp.text)
        sys.exit(1)

    print("\n2. Waiting for Execution (5s)...")
    time.sleep(5)

    print(f"\n3. Fetching PDF for Schedule {schedule_id}...")
    try:
        resp_pdf = requests.get(f"{BASE_URL}/execute/{schedule_id}/pdf")
        
        if resp_pdf.status_code == 200:
            content_len = len(resp_pdf.content)
            print(f"   SUCCESS: PDF received ({content_len} bytes)")
            if content_len > 1000:
                print("   Based on size, PDF seems valid!")
            else:
                print("   WARNING: PDF seems too small.")
        else:
            print(f"   FAILED: Status {resp_pdf.status_code}")
            print(resp_pdf.text)
            
    except Exception as e:
        print(f"   FAILED: {e}")

if __name__ == "__main__":
    test_execution()
