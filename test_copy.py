import copy
from app.database import SessionLocal
from app.models.variable_model import VariableModel

db = SessionLocal()
var = db.query(VariableModel).first()
if var:
    print(f"Original var: {var.name}, {var.type}")
    try:
        var_copy = copy.deepcopy(var)
        print(f"Copied var: {var_copy.name}")
        print(f"Hasattr type: {hasattr(var_copy, 'type')}")
    except Exception as e:
        print(f"Deepcopy error: {e}")
db.close()
