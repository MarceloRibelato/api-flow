from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
import json

class VariableCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    name: str
    faker_options: Optional[dict] = Field(default=None, alias="fakerOptions")

class VariableResponse(VariableCreate):
    id: int
    
    # Simulate the current configuration in the codebase
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

# Mock a database object (not a dict, an object with attributes)
class DBModel:
    def __init__(self):
        self.id = 1
        self.name = "Test"
        self.faker_options = {"length": 7}

db_obj = DBModel()

# Validate from attributes (ORM mode)
response = VariableResponse.model_validate(db_obj)

print("--- Pydantic Validation ---")
print(f"Object: {response}")
print(f"faker_options field: {response.faker_options}")

# Dump to JSON (Serialization)
print("\n--- JSON Dump (by_alias=True [Default]) ---")
print(response.model_dump_json(by_alias=True))

print("\n--- JSON Dump (by_alias=False) ---")
print(response.model_dump_json(by_alias=False))
