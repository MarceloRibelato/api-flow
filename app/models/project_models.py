from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


# Se você não for usar a tabela "project", remova a classe ProjectDB.
# Se for usar, ela PRECISA de uma chave primária como abaixo:
class ProjectDB(Base):
    __tablename__ = "project"
    id = Column(Integer, primary_key=True, index=True)  # Faltava isso!


# Esta é a classe que estamos usando nas rotas
class ProjectModel(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)  # Chave primária obrigatória
    name = Column(String(100), nullable=False)
    description = Column(Text)
    image_url = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    position = Column(Integer, default=0)
