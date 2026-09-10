import pytest
from unittest.mock import patch, MagicMock
from app.models.schedule_models import ScheduleModel
from app.models.api_test_history_models import MobileExecutionHistory
from app.schemas.history_schemas import ExecutionHistoryCreate
from app.services.history_service import HistoryService
from app.services.product_service import ProductService
from app.schemas.product_schemas import ProductMobileSettingsCreate
from app.models.product_models import ProductModel, ProductMobileDeviceDB
from app.models.company_models import CompanyDB

def test_mobile_execution_history_save_and_retrieve(db_session):
    """Test that mobile execution records are properly saved to MobileExecutionHistory."""
    from app.models.user_models import UserDB
    company = CompanyDB(name="Mobile Co")
    db_session.add(company)
    db_session.commit()

    user = UserDB(username="mobile_user", email="mob@test.com", hashed_password="pw", company_id=company.id, role="admin", status="active")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    hist_create = ExecutionHistoryCreate(
        batch_id="batch_mob_123",
        api_name="Mobile Click Login",
        project_id=1,
        flow_id="10",
        node_id="node_mob_1",
        method="MOBILE_ACTION",
        url="app://login",
        status_code=200,
        status_text="OK",
        response_time=150,
        execution_type="mobile"
    )

    saved = HistoryService.save(db_session, hist_create, user_id=user.id)
    assert saved is not None
    assert isinstance(saved, MobileExecutionHistory)
    assert saved.execution_type == "mobile"
    assert saved.node_id == "node_mob_1"

    # Query via HistoryService.get_all
    results = HistoryService.get_all(
        db=db_session,
        company_id=company.id,
        project_id=1,
        execution_type="mobile",
        batch_id="batch_mob_123"
    )
    assert results["total"] >= 1
    assert any(item.batch_id == "batch_mob_123" for item in results["items"])

def test_mobile_save_batch_auto_resolves_from_schedule(db_session):
    """Test that save_batch auto-detects execution_type='mobile' from schedule.flow_type."""
    sched = ScheduleModel(
        name="Mobile Scheduled Run",
        type="flow",
        target_id=10,
        environment_id=1,
        status="active",
        flow_type="mobile",
        company_id=1,
        user_id=1
    )
    db_session.add(sched)
    db_session.commit()
    db_session.refresh(sched)

    item = ExecutionHistoryCreate(
        batch_id=f"batch_sched_{sched.id}",
        api_name="Mobile Type Password",
        project_id=1,
        schedule_id=sched.id,
        method="MOBILE_ACTION",
        url="app://input",
        status_code=200,
        execution_type=None
    )

    HistoryService.save_batch(db_session, [item], user_id=1)

    mob_records = db_session.query(MobileExecutionHistory).filter(
        MobileExecutionHistory.schedule_id == sched.id
    ).all()
    assert len(mob_records) == 1
    assert mob_records[0].execution_type == "mobile"

def test_mobile_settings_crud(db_session):
    """Test mobile settings and mobile device matrix CRUD via ProductService and DB."""
    company = CompanyDB(name="Test Co")
    db_session.add(company)
    db_session.commit()

    product = ProductModel(name="Mobile Product", company_id=company.id, platform="mobile")
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)

    # 1. Get default settings
    settings = ProductService.get_mobile_settings(db_session, product.id)
    assert settings is not None
    assert settings.provider == "appium_local"

    # 2. Update settings
    update_data = ProductMobileSettingsCreate(
        provider="browserstack",
        server_url="https://hub.browserstack.com/wd/hub",
        auth_user="bs_user",
        auth_token="bs_token",
        device_name="Google Pixel 7",
        platform_version="13.0"
    )
    updated = ProductService.update_mobile_settings(db_session, product.id, update_data)
    assert updated.provider == "browserstack"
    assert updated.device_name == "Google Pixel 7"

    # 3. Add device to matrix
    device = ProductMobileDeviceDB(
        product_id=product.id,
        label="Samsung Galaxy S23",
        device_name="Samsung Galaxy S23",
        platform="android",
        platform_version="13.0",
        is_active=True
    )
    db_session.add(device)
    db_session.commit()

    # 4. List devices
    devices = db_session.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.product_id == product.id
    ).all()
    assert len(devices) == 1
    assert devices[0].device_name == "Samsung Galaxy S23"
