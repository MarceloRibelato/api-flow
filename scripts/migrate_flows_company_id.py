import sys
import os
# Adicionar o diretório atual ao sys.path para importar os módulos do app
sys.path.append(os.path.abspath(os.path.dirname(__file__) + "/.."))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.flow_models import FlowDB
from app.models.feature_models import FeatureModel
from app.models.product_models import ProductModel

def migrate_company_id():
    db = SessionLocal()
    try:
        # Buscar fluxos que não têm company_id
        flows_to_fix = db.query(FlowDB).filter(FlowDB.company_id == None).all()
        print(f"Encontrados {len(flows_to_fix)} fluxos para atualizar.")
        
        updated_count = 0
        for flow in flows_to_fix:
            # Tentar encontrar a empresa através da feature -> produto
            feature = db.query(FeatureModel).filter(FeatureModel.id == flow.project_id).first()
            if feature and feature.product_id:
                product = db.query(ProductModel).filter(ProductModel.id == feature.product_id).first()
                if product and product.company_id:
                    flow.company_id = product.company_id
                    updated_count += 1
            
        db.commit()
        print(f"Migração concluída: {updated_count} fluxos atualizados.")
        
    except Exception as e:
        print(f"Erro durante a migração: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate_company_id()
