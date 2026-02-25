import sys
import os

# Add the project root to sys.path
sys.path.append(os.getcwd())

from app.database import SessionLocal
from app.models.environment_model import Environment
from app.models.product_models import ProductModel

db = SessionLocal()
try:
    project_id = 1
    project = db.query(ProductModel).filter(ProductModel.id == project_id).first()
    if not project:
        print(f"ERRO: Projeto com ID {project_id} não encontrado.")
    else:
        print(f"Sucesso: Projeto '{project.name}' (ID {project_id}) encontrado.")
        
        envs = db.query(Environment).filter(Environment.project_id == project_id).all()
        print(f"Ambientes encontrados para o projeto {project_id}: {len(envs)}")
        for env in envs:
            print(f"- ID: {env.id}, Nome: {env.name}")
finally:
    db.close()
