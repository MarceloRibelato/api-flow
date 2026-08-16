from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.models.company_models import CompanyDB
from app.schemas.company_schemas import CompanySettingsUpdate, CompanySettingsResponse

router = APIRouter(prefix="/company", tags=["Company Settings"])

@router.get("/settings", response_model=CompanySettingsResponse)
def get_company_settings(
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User does not belong to a company")
        
    company = db.query(CompanyDB).filter(CompanyDB.id == current_user.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
        
    return company

@router.put("/settings", response_model=CompanySettingsResponse)
def update_company_settings(
    settings_data: CompanySettingsUpdate,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="User does not belong to a company")
        
    company = db.query(CompanyDB).filter(CompanyDB.id == current_user.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
        
    if settings_data.history_retention_days is not None:
        company.history_retention_days = settings_data.history_retention_days
    if settings_data.history_archive_retention_days is not None:
        company.history_archive_retention_days = settings_data.history_archive_retention_days
        
    db.commit()
    db.refresh(company)
    
    return company
