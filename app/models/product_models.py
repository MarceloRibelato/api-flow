from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class ProductModel(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Tenant
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    company_rel = relationship("CompanyDB", back_populates="products")

    # Relationship to features
    features = relationship("FeatureModel", back_populates="product", cascade="all, delete-orphan")
