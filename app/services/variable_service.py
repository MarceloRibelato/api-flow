from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.models.variable_model import Variable
from app.schemas.variable_schemas import VariableCreate


class VariableService:
    @staticmethod
    def get_all(db: Session, project_id: int, environment_id: Optional[int] = None, suite_id: Optional[str] = None):
        query = db.query(Variable).filter(Variable.project_id == project_id)

        if environment_id is not None:
            query = query.filter(Variable.environment_id == environment_id)
        else:
            query = query.filter(Variable.environment_id == None)

        if suite_id:
            query = query.filter((Variable.suite_id == suite_id) | (Variable.suite_id == None) | (Variable.suite_id == ""))
        else:
            query = query.filter((Variable.suite_id == None) | (Variable.suite_id == ""))

        all_vars = query.order_by(Variable.id.asc()).all()
        # Deduplicate variables by normalized uppercase name:
        # 1. Suite-specific variable overrides general (suite_id=None) variable
        # 2. For same specificity, the newer variable (higher id) takes precedence
        deduped = {}
        for v in all_vars:
            key = (v.name or "").strip().upper()
            if not key:
                continue
            if key not in deduped:
                deduped[key] = v
            else:
                existing = deduped[key]
                if suite_id and v.suite_id == suite_id and (not existing.suite_id or existing.suite_id != suite_id):
                    deduped[key] = v
                elif (v.suite_id == suite_id) == (existing.suite_id == suite_id):
                    deduped[key] = v
        return list(deduped.values())

    @staticmethod
    def get_by_id(db: Session, var_id: int):
        return db.query(Variable).filter(Variable.id == var_id).first()

    @staticmethod
    def create(db: Session, var: VariableCreate):
        # Normalize name to UPPERCASE to prevent duplicates (Token vs TOKEN)
        if var.name:
            var.name = var.name.strip().upper()

        query = db.query(Variable).filter(
            Variable.name == var.name,
            Variable.project_id == var.project_id,
            Variable.environment_id == var.environment_id,
        )
        existing_list = query.all()

        if existing_list:
            target = next((x for x in existing_list if x.suite_id == var.suite_id), existing_list[0])
            # If secret variable and incoming value is empty or masked, preserve existing value
            if var.type == "secret" and (not var.value or var.value.strip() in ("", "••••••••", "••••••••••••", "********")):
                pass
            else:
                target.value = var.value
            target.type = var.type
            target.faker_type = var.faker_type
            target.faker_options = var.faker_options
            target.json_path = var.json_path
            target.api_id = var.api_id
            target.suite_id = var.suite_id
            target.updated_at = func.now()
            # Clean up any duplicate rows with the same name in this environment
            for other in existing_list:
                if other.id != target.id:
                    db.delete(other)
            db.commit()
            db.refresh(target)
            return target
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
                suite_id=var.suite_id,
            )
            db.add(db_var)
            db.commit()
            db.refresh(db_var)
            return db_var

    @staticmethod
    def update(db: Session, var_id: int, var_data: VariableCreate):
        # Normalize name to UPPERCASE
        if var_data.name:
            var_data.name = var_data.name.strip().upper()

        db_var = db.query(Variable).filter(Variable.id == var_id).first()
        if db_var:
            db_var.name = var_data.name
            # If secret variable and incoming value is empty or masked, preserve existing value
            if var_data.type == "secret" and (not var_data.value or var_data.value.strip() in ("", "••••••••", "••••••••••••", "********")):
                pass
            else:
                db_var.value = var_data.value
            db_var.type = var_data.type
            db_var.faker_type = var_data.faker_type
            db_var.faker_options = var_data.faker_options
            db_var.json_path = var_data.json_path
            db_var.api_id = var_data.api_id
            db_var.project_id = var_data.project_id
            db_var.flow_id = var_data.flow_id
            db_var.environment_id = var_data.environment_id
            db_var.suite_id = var_data.suite_id
            
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
        db: Session, name: str, project_id: int, environment_id: Optional[int] = None, suite_id: Optional[str] = None
    ):
        query = db.query(Variable).filter(
            Variable.name == name, Variable.project_id == project_id
        )

        if environment_id is not None:
            query = query.filter(Variable.environment_id == environment_id)
        else:
            query = query.filter(Variable.environment_id == None)

        if suite_id:
            query = query.filter(Variable.suite_id == suite_id)
        else:
            query = query.filter((Variable.suite_id == None) | (Variable.suite_id == ""))

        db_var = query.first()

        if db_var:
            db.delete(db_var)
            db.commit()
            return True
        return False  # Idempotent approach usually returns success, but service returns status logic
