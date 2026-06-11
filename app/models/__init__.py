# Import all models to ensure they are registered with SQLAlchemy
from app.models.user_models import UserDB
from app.models.company_models import CompanyDB
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
from app.models.flow_models import FlowDB
from app.models.variable_model import Variable
from app.models.environment_model import Environment
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.schedule_models import ScheduleModel
from app.models.auth_models import BlacklistedToken
from app.models.service_token_models import ServiceTokenDB
from app.models.performance_models import PerformanceTestResult
from app.models.integration_models import ProjectIntegrationDB

__all__ = [
    "UserDB",
    "CompanyDB",
    "ProductModel",
    "FeatureModel",
    "FlowDB",
    "Variable",
    "Environment",
    "ApiExecutionHistory",
    "ScheduleModel",
    "BlacklistedToken",
    "ServiceTokenDB",
    "PerformanceTestResult",
    "ProjectIntegrationDB",
]
