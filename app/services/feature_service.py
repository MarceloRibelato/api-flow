from sqlalchemy.orm import Session

from app.models.feature_models import FeatureModel
from app.schemas.feature_schemas import FeatureCreate, ReorderSchema


class FeatureService:
    @staticmethod
    def create(db: Session, feature: FeatureCreate):
        db_feature = FeatureModel(**feature.model_dump())
        db.add(db_feature)
        db.commit()
        db.refresh(db_feature)
        return db_feature

    @staticmethod
    def get_all(db: Session, product_id: int = None):
        query = db.query(FeatureModel)
        if product_id:
            query = query.filter(FeatureModel.product_id == product_id)
        return query.order_by(FeatureModel.position.asc()).all()

    @staticmethod
    def get_by_id(db: Session, feature_id: int):
        return db.query(FeatureModel).filter(FeatureModel.id == feature_id).first()

    @staticmethod
    def update(db: Session, feature_id: int, feature_update: FeatureCreate):
        db_feature = (
            db.query(FeatureModel).filter(FeatureModel.id == feature_id).first()
        )
        if not db_feature:
            return None

        update_data = feature_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_feature, key, value)

        db.commit()
        db.refresh(db_feature)
        return db_feature

    @staticmethod
    def delete(db: Session, feature_id: int):
        db_feature = (
            db.query(FeatureModel).filter(FeatureModel.id == feature_id).first()
        )
        if not db_feature:
            return False

        db.delete(db_feature)
        db.commit()
        return True

    @staticmethod
    def reorder(db: Session, data: ReorderSchema):
        try:
            for item in data.new_order:
                db.query(FeatureModel).filter(FeatureModel.id == item.id).update(
                    {"position": item.position}
                )
            db.commit()
            return True
        except Exception:
            db.rollback()
            return False
