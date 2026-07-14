from pydantic import BaseModel
from typing import List, Dict

class ApiCallSchema(BaseModel):
    headers: List[Dict[str, str]]

class CardData(BaseModel):
    apiCalls: List[ApiCallSchema]

class FlowSaveSchema(BaseModel):
    cardData: Dict[str, CardData]

payload = {
    "cardData": {
        "node_1": {
            "apiCalls": '[{"headers": "{\\"Authorization\\": \\"Bearer\\"}"}]'
        }
    }
}

try:
    FlowSaveSchema(**payload)
    print("Success")
except Exception as e:
    print(e)
