import sys
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add current dir to path
sys.path.append(os.getcwd())

try:
    from app.database import Base, get_db, engine
    from app.models.environment_model import Environment
    from app.models.variable_model import Variable
except ImportError:
    # Manual fallback if app module not found
    print("App module not found in path, trying manual setup")
    sys.exit(1)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

print("-" * 50)
print("ENVIRONMENTS:")
envs = db.query(Environment).all()
for e in envs:
    count = db.query(Variable).filter(Variable.environment_id == e.id).count()
    print(f"ID: {e.id} | Name: {e.name} | Vars: {count}")

print("-" * 50)
print("VARIABLES (Top 20):")
vars = db.query(Variable).limit(20).all()
for v in vars:
    print(f"ID: {v.id} | Name: {v.name} | EnvID: {v.environment_id} | FlowID: {v.flow_id}")

print("-" * 50)
