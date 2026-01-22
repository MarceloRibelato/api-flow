from sqlalchemy.orm import Session

from app.models.environment_model import Environment
from app.models.variable_model import Variable
from app.schemas.environment_schemas import EnvironmentCreate


class EnvironmentService:
    @staticmethod
    def get_by_project(db: Session, project_id: int):
        return db.query(Environment).filter(Environment.project_id == project_id).all()

    @staticmethod
    def get_by_id(db: Session, env_id: int):
        return db.query(Environment).filter(Environment.id == env_id).first()

    @staticmethod
    def create(db: Session, env: EnvironmentCreate):
        # 1. Create the new environment
        db_env = Environment(name=env.name, project_id=env.project_id)
        db.add(db_env)
        db.commit()
        db.refresh(db_env)

        # 2. Handle Cloning Logic
        if env.clone_from_id:
            # Find variables in the source environment
            source_vars = (
                db.query(Variable)
                .filter(Variable.environment_id == env.clone_from_id)
                .all()
            )

            for var in source_vars:
                new_var = Variable(
                    name=var.name,
                    value=var.value,
                    type=var.type,
                    faker_type=var.faker_type,
                    faker_options=var.faker_options, # Copy options!
                    json_path=var.json_path,
                    api_id=var.api_id,
                    project_id=env.project_id,  # Should match new env's project
                    flow_id=var.flow_id,
                    environment_id=db_env.id,  # Assign to new environment
                )
                db.add(new_var)

            db.commit()

        return db_env

    @staticmethod
    def delete(db: Session, env_id: int):
        db_env = db.query(Environment).filter(Environment.id == env_id).first()
        if db_env:
            db.delete(db_env)
            db.commit()
            return True
        return False
