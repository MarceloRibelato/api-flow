import json

card_data = {
    "apiCalls": [
        {"headers": {"Authorization": "Bearer expiredtoken"}},
        {"headers": '{"Authorization": "Bearer token"}'}
    ]
}

all_api_calls_lists = []
if "apiCalls" in card_data:
    all_api_calls_lists.append(card_data.get("apiCalls", []))

for api_calls_list in all_api_calls_lists:
    for api_call in api_calls_list:
        for field in ["headers", "params"]:
            if field in api_call:
                val = api_call[field]
                new_val = []
                if isinstance(val, dict):
                    new_val = [{"key": str(k), "value": str(v)} for k, v in val.items()]
                elif isinstance(val, list):
                    pass
                elif isinstance(val, str):
                    try:
                        parsed = json.loads(val)
                        if isinstance(parsed, dict):
                            new_val = [{"key": str(k), "value": str(v)} for k, v in parsed.items()]
                    except:
                        pass
                api_call[field] = new_val

print(json.dumps(card_data, indent=2))
