from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class VariableCreate(BaseModel):
    name: str
    value: str = ""
    # ...
    api_id: Optional[int] = Field(default=None, alias="apiId")
    # ...


class VariableResponse(VariableCreate):
    id: int
    updated_at: Optional[datetime] = None
    faker_type: Optional[str] = Field(default=None)

    class Config:
        from_attributes = True


def test_serialization():
    # Simulate DB object
    class MockDB:
        id = 1
        name = "test"
        value = "val"
        type = "static"
        faker_type = None
        api_id = 1234567890123  # BigInt
        project_id = 1
        flow_id = None
        environment_id = None
        json_path = None
        created_at = datetime.now()
        updated_at = datetime.now()

    db_obj = MockDB()

    # Serialize
    model = VariableResponse.model_validate(db_obj)

    # Dump mostly like FastAPI does (usually by_alias=True is NOT default for model_dump, but FastAPI uses it?
    # Actually FastAPI uses jsonable_encoder which might use by_alias=True if configured?
    # Let's check both.)

    print(f"By Alias=False: {model.model_dump_json(by_alias=False)}")
    print(f"By Alias=True:  {model.model_dump_json(by_alias=True)}")


if __name__ == "__main__":
    test_serialization()
