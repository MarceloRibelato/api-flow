from sqlalchemy.orm import Session
from app.models.product_models import ProductModel
from app.schemas.product_schemas import ProductCreate

class ProductService:
    @staticmethod
    def list(db: Session, skip: int = 0, limit: int = 100):
        return db.query(ProductModel).order_by(ProductModel.name).offset(skip).limit(limit).all()

    @staticmethod
    def create(db: Session, product: ProductCreate):
        db_product = ProductModel(
            name=product.name,
            description=product.description,
            image_url=product.image_url
        )
        db.add(db_product)
        db.commit()
        db.refresh(db_product)
        return db_product

    @staticmethod
    def get(db: Session, product_id: int):
        return db.query(ProductModel).filter(ProductModel.id == product_id).first()

    @staticmethod
    def delete(db: Session, product_id: int):
        db_product = db.query(ProductModel).filter(ProductModel.id == product_id).first()
        if db_product:
            db.delete(db_product)
            db.commit()
            return True
        return False

    @staticmethod
    def update(db: Session, product_id: int, product_data: ProductCreate):
        db_product = db.query(ProductModel).filter(ProductModel.id == product_id).first()
        if db_product:
            db_product.name = product_data.name
            db_product.description = product_data.description
            db_product.image_url = product_data.image_url
            db.commit()
            db.refresh(db_product)
            return db_product
        return None
