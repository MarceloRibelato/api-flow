from app.schemas.history_schemas import PaginatedHistoryResponse, ExecutionHistorySummary, ExecutionHistoryResponse

print("Checking ExecutionHistorySummary fields:")
fields = ExecutionHistorySummary.model_fields.keys()
print(list(fields))

# Verify that sensitive/large fields are NOT present
forbidden_fields = ['request_body', 'response_body', 'request_headers', 'response_headers']
for f in forbidden_fields:
    if f in fields:
        print(f"FAIL: {f} is present in Summary!")
    else:
        print(f"PASS: {f} is NOT in Summary.")

print("\nChecking PaginatedHistoryResponse item type:")
# Just print it out to visually verify
print(f"Items type: {PaginatedHistoryResponse.model_fields['items'].annotation}")
