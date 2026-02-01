from sqlalchemy.orm import Session
from app.models.product_models import ProductModel
from app.schemas.product_schemas import ProductCreate

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
            db_product.notification_urls = product_data.notification_urls
            db_product.channel_type = product_data.channel_type
            db.commit()
            db.refresh(db_product)
            return db_product
        return None
