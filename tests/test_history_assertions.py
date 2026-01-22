from app.schemas.history_schemas import ExecutionHistoryCreate, AssertionResult
from app.services.history_service import HistoryService
# Assuming db_session fixture or similar is available if running as pytest
# For standalone script we might need manual setup, but let's try to stick to existing pytest infrastructure first.
# If this fails I will create a standalone script.

def test_save_execution_with_assertions(client, db):
    # Setup
    user_id = 999 
    
    # Assertions
    assertions = [
        AssertionResult(
            source="status_code",
            operator="eq",
            target=200,
            actual=200,
            success=True
        ),
        AssertionResult(
            source="body.id",
            operator="not_null",
            actual=123,
            success=True
        ),
        AssertionResult(
            source="response_time",
            operator="lt",
            target=500,
            actual=600,
            success=False,
            error_message="Response time too high"
        )
    ]

    history_data = ExecutionHistoryCreate(
        method="GET",
        url="http://api.test/assertions",
        status_code=200,
        status_text="OK",
        response_time=600,
        assertions=assertions
    )

    # Act
    try:
        saved = HistoryService.save(db, history_data, user_id)
        print("Save successful")
    except Exception as e:
        print(f"ERROR SAVING HISTORY: {e}")
        import traceback
        traceback.print_exc()
        raise e

    # Assert
    assert saved.id is not None
    assert saved.assertions is not None
    assert len(saved.assertions) == 3
    
    # Check if data persisted correctly
    assert saved.assertions[0]["source"] == "status_code"
    assert saved.assertions[2]["success"] is False
