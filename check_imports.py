import os
import sys

sys.path.append(os.getcwd())
try:
    # Routes

    # Schemas

    # Services

    print("Import successful")
except Exception as e:
    import traceback

    traceback.print_exc()
    print(f"Import failed: {e}")
