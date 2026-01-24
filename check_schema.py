import sys
import os

# Add current directory to path so imports work
sys.path.append(os.getcwd())

from app.database import Base, engine
# Import all models to ensure they are registered with Base
from app.models import flow_models, feature_models, api_test_history_models, environment_model, variable_model, user_models

try:
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully.")
except Exception as e:
    with open("schema_error.txt", "w") as f:
        f.write(str(e))
    print(f"Error creating tables: {e}")
    sys.exit(1)
