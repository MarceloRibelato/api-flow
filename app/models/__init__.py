# Import all models to ensure they are registered with SQLAlchemy
from app.models.user_models import UserDB
from app.models.company_models import CompanyDB
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
from app.models.flow_models import FlowDB
from app.models.variable_model import Variable
from app.models.environment_model import Environment
from app.models.api_test_history_models import (
    ApiExecutionHistory,
    WebExecutionHistory,
    MobileExecutionHistory
)
from app.models.schedule_models import ScheduleModel
from app.models.auth_models import BlacklistedToken
from app.models.service_token_models import ServiceTokenDB
from app.models.performance_models import PerformanceTestResult
from app.models.integration_models import ProjectIntegrationDB
from app.models.team_models import TeamDB
from app.models.mock_models import ServiceMockDB, MockRuleDB, MockLogDB
from app.models.audit_models import AuditLogDB
from app.models.hitl_models import HitlSessionDB
from app.models.agent_models import AgentSettingsDB, AgentMemoryDB
from app.models.front_recording_models import FrontRecordingDB

__all__ = [
    "UserDB",
    "CompanyDB",
    "TeamDB",
    "ServiceMockDB",
    "MockRuleDB",
    "MockLogDB",
    "ProductModel",
    "FeatureModel",
    "FlowDB",
    "Variable",
    "Environment",
    "ApiExecutionHistory",
    "WebExecutionHistory",
    "MobileExecutionHistory",
    "ScheduleModel",
    "BlacklistedToken",
    "ServiceTokenDB",
    "PerformanceTestResult",
    "ProjectIntegrationDB",
    "AuditLogDB",
    "HitlSessionDB",
    "AgentSettingsDB",
    "AgentMemoryDB",
    "FrontRecordingDB",
]
