from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base
# Note: 'ProductModel' is referenced by string in relationship to avoid circular imports.


# Se você não for usar a tabela "project", remova a classe ProjectDB.
# Se for usar, ela PRECISA de uma chave primária como abaixo:
# Se você não for usar a tabela "project", remova a classe ProjectDB.
# Se for usar, ela PRECISA de uma chave primária como abaixo:
class ProjectDB(Base):
    __tablename__ = "project"
    id = Column(Integer, primary_key=True, index=True)  # Faltava isso!


# Esta é a classe que estamos usando nas rotas
class FeatureModel(Base):
    __tablename__ = "features"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    image_url = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    position = Column(Integer, default=0)
    
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=True) # Start nullable, but logic will enforce it
    
    # Relationship
    product = relationship("ProductModel", back_populates="features")

