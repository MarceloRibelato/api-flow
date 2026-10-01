from sqlalchemy.orm import Session

from app.models.environment_model import Environment
from app.models.variable_model import Variable
from app.schemas.environment_schemas import EnvironmentCreate


class EnvironmentService:
    @staticmethod
    def get_by_project(db: Session, project_id: int, suite_id: str = None):
        query = db.query(Environment).filter(Environment.project_id == project_id)
        if suite_id:
            query = query.filter(Environment.suite_id == suite_id)
        else:
            query = query.filter((Environment.suite_id == None) | (Environment.suite_id == ""))
        return query.all()

    @staticmethod
    def get_by_id(db: Session, env_id: int):
        return db.query(Environment).filter(Environment.id == env_id).first()

    @staticmethod
    def create(db: Session, env: EnvironmentCreate):
        # 1. Create the new environment
        db_env = Environment(name=env.name, project_id=env.project_id, suite_id=env.suite_id)
        db.add(db_env)
        db.commit()
        db.refresh(db_env)

        # 2. Handle Cloning Logic
        if env.clone_from_id:
            source_env = db.query(Environment).filter(Environment.id == env.clone_from_id).first()
            if source_env and source_env.base_url and not db_env.base_url:
                db_env.base_url = source_env.base_url
                db.commit()

            # Find variables in the source environment
            query = db.query(Variable).filter(Variable.environment_id == env.clone_from_id)
            if env.suite_id:
                query = query.filter((Variable.suite_id == env.suite_id) | (Variable.suite_id == None) | (Variable.suite_id == ""))
            source_vars = query.all()

            for var in source_vars:
                target_suite_id = env.suite_id if env.suite_id else var.suite_id
                new_var = Variable(
                    name=var.name,
                    value=var.value,
                    type=var.type,
                    faker_type=var.faker_type,
                    faker_options=var.faker_options,
                    json_path=var.json_path,
                    api_id=var.api_id,
                    project_id=env.project_id,
                    flow_id=var.flow_id,
                    suite_id=target_suite_id,
                    environment_id=db_env.id,
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

    @staticmethod
    def update(db: Session, env_id: int, update_data: dict):
        db_env = db.query(Environment).filter(Environment.id == env_id).first()
        if db_env:
            for key, value in update_data.items():
                if hasattr(db_env, key):
                    setattr(db_env, key, value)
            db.commit()
            db.refresh(db_env)
        return db_env
