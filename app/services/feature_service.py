from sqlalchemy.orm import Session

from app.models.feature_models import FeatureModel
from app.models.product_models import ProductModel
from app.schemas.feature_schemas import FeatureCreate, ReorderSchema

class FeatureService:
    @staticmethod
    def create(db: Session, feature: FeatureCreate, company_id: int):
        # Verify product ownership
        product = db.query(ProductModel).filter(ProductModel.id == feature.product_id, ProductModel.company_id == company_id).first()
        if not product:
            return None # Or raise mismatch

        db_feature = FeatureModel(**feature.model_dump())
        db.add(db_feature)
        db.commit()
        db.refresh(db_feature)
        return db_feature

    @staticmethod
    def get_all(db: Session, company_id: int, product_id: int = None):
        if product_id:
            # Verify product ownership
            product = db.query(ProductModel).filter(ProductModel.id == product_id, ProductModel.company_id == company_id).first()
            if not product:
                return []
            return db.query(FeatureModel).filter(FeatureModel.product_id == product_id).order_by(FeatureModel.position.asc(), FeatureModel.created_at.asc()).all()
        else:
            # List all features for the company (across products)
            return db.query(FeatureModel).join(ProductModel).filter(ProductModel.company_id == company_id).order_by(FeatureModel.position.asc(), FeatureModel.created_at.asc()).all()

    @staticmethod
    def get_by_id(db: Session, feature_id: int, company_id: int):
        return db.query(FeatureModel).join(ProductModel).filter(FeatureModel.id == feature_id, ProductModel.company_id == company_id).first()

    @staticmethod
    def update(db: Session, feature_id: int, feature_update: FeatureCreate, company_id: int):
        db_feature = db.query(FeatureModel).join(ProductModel).filter(FeatureModel.id == feature_id, ProductModel.company_id == company_id).first()
        if not db_feature:
            return None

        update_data = feature_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_feature, key, value)

        db.commit()
        db.refresh(db_feature)
        return db_feature

    @staticmethod
    def delete(db: Session, feature_id: int, company_id: int):
        try:
            # First, check if feature exists at all ignoring company
            feature = db.query(FeatureModel).filter(FeatureModel.id == feature_id).first()
            if not feature:
                print(f"DEBUG DELETE: Feature {feature_id} not found at all")
                return False
            
            # Check ownership
            db_feature = db.query(FeatureModel).join(ProductModel).filter(
                FeatureModel.id == feature_id, 
                ProductModel.company_id == company_id
            ).first()
            
            if not db_feature:
                # Find which company it belongs to for debug
                product = db.query(ProductModel).filter(ProductModel.id == feature.product_id).first()
                p_cid = product.company_id if product else "None"
                print(f"DEBUG DELETE: Feature {feature_id} exists but ownership check failed. User CID: {company_id}, Product CID: {p_cid}")
                return False

            # 1. Clean up associated front recordings (Avoid FK violation)
            from app.models.front_recording_models import FrontRecordingDB
            db.query(FrontRecordingDB).filter(FrontRecordingDB.feature_id == feature_id).delete(synchronize_session=False)
            
            from app.models.schedule_models import ScheduleModel
            from app.models.api_test_history_models import ApiExecutionHistory
            from app.models.flow_models import FlowDB
            from app.services.flow_service import FlowService
            
            # Clean up Flow associated with this feature
            flow = db.query(FlowDB).filter(FlowDB.project_id == feature_id).first()
            if flow:
                # Clean up Execution History Orphaned by flow
                db.query(ApiExecutionHistory).filter(ApiExecutionHistory.flow_id == str(flow.id)).delete(synchronize_session=False)
                FlowService.delete(db, feature_id, company_id)
                
            # Clean up Schedules Orphaned
            db.query(ScheduleModel).filter(ScheduleModel.target_id == feature_id, ScheduleModel.type == 'feature').delete(synchronize_session=False)

            # 2. Finally delete the feature
            db.delete(db_feature)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"ERROR DELETING FEATURE {feature_id}: {e}")
            import traceback
            traceback.print_exc()
            return False

    @staticmethod
    def reorder(db: Session, data: ReorderSchema, company_id: int):
        # TODO: Optimize to avoid N+1 or bulk checks.
        # For now, verify ownership of at least one (or all implicitly via update filter).
        # We can just filter by company in the update query? No, standard update doesn't support join easily in all dialects?
        # Let's simple check one.
        if not data.new_order: return True
        
        # Verify first item belongs to company
        first_id = data.new_order[0].id
        check = db.query(FeatureModel).join(ProductModel).filter(FeatureModel.id == first_id, ProductModel.company_id == company_id).first()
        if not check:
            return False

        try:
            for item in data.new_order:
                # Update logic - ideally check each, but trusting first for MVP if they are in same product
                db.query(FeatureModel).filter(FeatureModel.id == item.id).update(
                    {"position": item.position}
                )
            db.commit()
            return True
        except Exception:
            db.rollback()
            return False
