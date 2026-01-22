from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.models.variable_model import Variable
from app.schemas.variable_schemas import VariableCreate


class VariableService:
    @staticmethod
    def get_all(db: Session, project_id: int, environment_id: Optional[int] = None):
        query = db.query(Variable).filter(Variable.project_id == project_id)

        if environment_id is not None:
            query = query.filter(Variable.environment_id == environment_id)
        else:
            query = query.filter(Variable.environment_id == None)

        return query.all()

    @staticmethod
    def get_by_id(db: Session, var_id: int):
        return db.query(Variable).filter(Variable.id == var_id).first()

    @staticmethod
    def create(db: Session, var: VariableCreate):
        existing = (
            db.query(Variable)
            .filter(
                Variable.name == var.name,
                Variable.project_id == var.project_id,
                Variable.environment_id == var.environment_id,
            )
            .first()
        )

        if existing:
            existing.value = var.value
            existing.type = var.type
            existing.faker_type = var.faker_type
            existing.faker_options = var.faker_options
            existing.json_path = var.json_path
            existing.api_id = var.api_id
            db.commit()
            db.refresh(existing)
            return existing
        else:
            db_var = Variable(
                name=var.name,
                value=var.value,
                type=var.type,
                faker_type=var.faker_type,
                faker_options=var.faker_options,
                project_id=var.project_id,
                flow_id=var.flow_id,
                environment_id=var.environment_id,
                api_id=var.api_id,
                json_path=var.json_path,
            )
            db.add(db_var)
            db.commit()
            db.refresh(db_var)
            return db_var

    @staticmethod
    def update(db: Session, var_id: int, var_data: VariableCreate):
        db_var = db.query(Variable).filter(Variable.id == var_id).first()
        if db_var:
            db_var.name = var_data.name
            db_var.value = var_data.value
            db_var.type = var_data.type
            db_var.faker_type = var_data.faker_type
            db_var.faker_options = var_data.faker_options
            db_var.json_path = var_data.json_path
            db_var.api_id = var_data.api_id
            db_var.project_id = var_data.project_id
            db_var.flow_id = var_data.flow_id
            db_var.environment_id = var_data.environment_id
            
            # Explicitly touch updated_at to ensure sort order changes
            db_var.updated_at = func.now()

            db.commit()
            db.refresh(db_var)
            return db_var
        return None

    @staticmethod
    def bulk_create(db: Session, vars: List[VariableCreate]):
        results = []
        for var in vars:
            saved_var = VariableService.create(db, var)
            results.append(saved_var)
        return results

    @staticmethod
    def delete(db: Session, var_id: int):
        db_var = db.query(Variable).filter(Variable.id == var_id).first()
        if db_var:
            db.delete(db_var)
            db.commit()
            return True
        return False

    @staticmethod
    def delete_by_name(
        db: Session, name: str, project_id: int, environment_id: Optional[int] = None
    ):
        query = db.query(Variable).filter(
            Variable.name == name, Variable.project_id == project_id
        )

        if environment_id is not None:
            query = query.filter(Variable.environment_id == environment_id)
        else:
            query = query.filter(Variable.environment_id == None)

        db_var = query.first()

        if db_var:
            db.delete(db_var)
            db.commit()
            return True
        return False  # Idempotent approach usually returns success, but service returns status logic
