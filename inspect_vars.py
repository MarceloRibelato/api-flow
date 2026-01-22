import sys
import os

# Add the project root to sys.path
sys.path.append("c:/Projetos/Flow/api-flow")

from app.database import SessionLocal
# Import ALL models to ensure relationships are resolved
from app.models.environment_model import Environment
from app.models.variable_model import Variable

def inspect_variables():
    db = SessionLocal()
    try:
        vars = db.query(Variable).all()
        print(f"Total Variables: {len(vars)}")
        
        for v in vars:
            print(f"Variable: {v.name}, ID: {v.id}, ProjectID: {v.project_id}, EnvID: {v.environment_id}")

    finally:
        db.close()

if __name__ == "__main__":
    inspect_variables()
