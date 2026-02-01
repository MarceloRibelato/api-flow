
import sys
import os

# Add the directory containing 'app' to python path
sys.path.append(os.getcwd())

try:
    print("Attempting to import app.main...")
    from app.main import app
    print("Successfully imported app.main")
except Exception as e:
    print(f"FAILED to import app.main: {e}")
    import traceback
    traceback.print_exc()

import logging
logging.basicConfig(level=logging.INFO)


try:
    print("Attempting to import scheduler_service...")
    from app.services.scheduler_service import execute_job
    print("Successfully imported scheduler_service")
except Exception as e:
    print(f"FAILED to import scheduler_service: {e}")
    import traceback
    traceback.print_exc()
