from sqlalchemy import Column, Integer, String, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.database import Base

class ProjectIntegrationDB(Base):
    __tablename__ = "project_integrations"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    provider = Column(String(50), nullable=False) # 'jira', 'azure'
    config = Column(JSON, nullable=False) 
    
    # Optional relationship if needed later
    # product = relationship("ProductModel", back_populates="integrations")
