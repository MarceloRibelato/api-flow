from sqlalchemy.orm import Session

from app.models.project_models import ProjectModel
from app.schemas.project_schemas import ProjectCreate, ReorderSchema


class ProjectService:
    @staticmethod
    def create(db: Session, project: ProjectCreate):
        db_project = ProjectModel(**project.model_dump())
        db.add(db_project)
        db.commit()
        db.refresh(db_project)
        return db_project

    @staticmethod
    def get_all(db: Session):
        return db.query(ProjectModel).order_by(ProjectModel.position.asc()).all()

    @staticmethod
    def get_by_id(db: Session, project_id: int):
        return db.query(ProjectModel).filter(ProjectModel.id == project_id).first()

    @staticmethod
    def update(db: Session, project_id: int, project_update: ProjectCreate):
        db_project = (
            db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
        )
        if not db_project:
            return None

        update_data = project_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_project, key, value)

        db.commit()
        db.refresh(db_project)
        return db_project

    @staticmethod
    def delete(db: Session, project_id: int):
        db_project = (
            db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
        )
        if not db_project:
            return False

        db.delete(db_project)
        db.commit()
        return True

    @staticmethod
    def reorder(db: Session, data: ReorderSchema):
        try:
            for item in data.new_order:
                db.query(ProjectModel).filter(ProjectModel.id == item.id).update(
                    {"position": item.position}
                )
            db.commit()
            return True
        except Exception:
            db.rollback()
            return False
