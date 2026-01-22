import os
import sys

sys.path.append(os.getcwd())

from app.database import SessionLocal
from app.models.variable_model import Variable


def check_truncated_ids():
    db = SessionLocal()
    try:
        # Check for max int32 value
        bad_vars = db.query(Variable).filter(Variable.api_id == 2147483647).all()

        print(f"Found {len(bad_vars)} variables with truncated api_id (2147483647):")
        for var in bad_vars:
            print(
                f" - ID: {var.id}, Name: {var.name}, Environment: {var.environment_id}"
            )

    finally:
        db.close()


if __name__ == "__main__":
    check_truncated_ids()
