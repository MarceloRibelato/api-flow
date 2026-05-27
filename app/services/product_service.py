from sqlalchemy.orm import Session
from app.models.product_models import ProductModel, ProductMobileSettingsDB
from app.schemas.product_schemas import ProductCreate, ProductMobileSettingsCreate

class ProductService:
    @staticmethod
    def list(db: Session, company_id: int, skip: int = 0, limit: int = 100):
        return db.query(ProductModel).filter(ProductModel.company_id == company_id).order_by(ProductModel.created_at.asc()).offset(skip).limit(limit).all()

    @staticmethod
    def create(db: Session, product: ProductCreate, company_id: int):
        db_product = ProductModel(
            name=product.name,
            description=product.description,
            image_url=product.image_url,
            platform=product.platform,
            notification_urls=product.notification_urls,
            channel_type=product.channel_type,
            company_id=company_id
        )
        db.add(db_product)
        db.commit()
        db.refresh(db_product)
        return db_product

    @staticmethod
    def get(db: Session, product_id: int, company_id: int):
        return db.query(ProductModel).filter(ProductModel.id == product_id, ProductModel.company_id == company_id).first()

    @staticmethod
    def delete(db: Session, product_id: int, company_id: int):
        db_product = db.query(ProductModel).filter(ProductModel.id == product_id, ProductModel.company_id == company_id).first()
        if db_product:
            from app.models.feature_models import FeatureModel
            from app.models.schedule_models import ScheduleModel
            from app.models.api_test_history_models import ExecutionHistoryModel
            from app.models.flow_models import FlowDB
            from app.models.front_recording_models import FrontRecordingDB
            from app.services.flow_service import FlowService
            
            # Fetch all features to clean up their orphans since SQL cascade bypasses Python code
            features = db.query(FeatureModel).filter(FeatureModel.product_id == product_id).all()
            for feat in features:
                # Clean up flows
                flow = db.query(FlowDB).filter(FlowDB.project_id == feat.id).first()
                if flow:
                    FlowService.delete(db, feat.id, company_id)
                # Clean up front recordings
                db.query(FrontRecordingDB).filter(FrontRecordingDB.feature_id == feat.id).delete(synchronize_session=False)
                # Clean up feature schedules
                db.query(ScheduleModel).filter(ScheduleModel.target_id == feat.id, ScheduleModel.type == 'feature').delete(synchronize_session=False)

            # Clean up suite schedules
            db.query(ScheduleModel).filter(ScheduleModel.target_id == product_id, ScheduleModel.type == 'suite').delete(synchronize_session=False)
            
            # Clean up Execution History for the Product
            db.query(ExecutionHistoryModel).filter(ExecutionHistoryModel.project_id == product_id).delete(synchronize_session=False)
            
            db.delete(db_product)
            db.commit()
            return True
        return False

    @staticmethod
    def update(db: Session, product_id: int, product_data: ProductCreate, company_id: int):
        import logging
        logging.info(f"UPDATING PRODUCT {product_id} with channel_type={product_data.channel_type}")
        db_product = db.query(ProductModel).filter(ProductModel.id == product_id, ProductModel.company_id == company_id).first()
        if db_product:
            db_product.name = product_data.name
            db_product.description = product_data.description
            db_product.image_url = product_data.image_url
            db_product.platform = product_data.platform
            db_product.notification_urls = product_data.notification_urls
            db_product.channel_type = product_data.channel_type
            db.commit()
            db.refresh(db_product)
            return db_product
        return None

    @staticmethod
    def get_mobile_settings(db: Session, product_id: int):
        settings = db.query(ProductMobileSettingsDB).filter(ProductMobileSettingsDB.product_id == product_id).first()
        if not settings:
            # Auto-create default settings
            settings = ProductMobileSettingsDB(product_id=product_id, provider="appium_local")
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return settings

    @staticmethod
    def update_mobile_settings(db: Session, product_id: int, settings_data: ProductMobileSettingsCreate):
        settings = db.query(ProductMobileSettingsDB).filter(ProductMobileSettingsDB.product_id == product_id).first()
        if not settings:
            settings = ProductMobileSettingsDB(product_id=product_id)
            db.add(settings)
        
        settings.provider = settings_data.provider
        settings.server_url = settings_data.server_url
        settings.auth_user = settings_data.auth_user
        settings.auth_token = settings_data.auth_token
        settings.device_name = settings_data.device_name
        settings.platform_version = settings_data.platform_version
        settings.app_identifier = settings_data.app_identifier
        
        db.commit()
        db.refresh(settings)
        return settings

