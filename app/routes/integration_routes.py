from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.database import get_db
from app.services.integration_service import IntegrationService

router = APIRouter(prefix="/integrations", tags=["Integrations"])

@router.get("/project/{product_id}/{provider}/config")
def get_project_integration_config(product_id: int, provider: str, db: Session = Depends(get_db)):
    config = IntegrationService.get_project_integration(db, product_id, provider)
    if not config:
        # Return empty config instead of 404 to let frontend know it's not set
        return {"configured": False, "config": {}}
    
    # Hide token for security when returning
    safe_config = config.copy()
    if "token" in safe_config:
        safe_config["token"] = "********"
        
    return {"configured": True, "config": safe_config}

@router.post("/project/{product_id}/{provider}/config")
def save_project_integration_config(product_id: int, provider: str, config: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    # If token is masked, we should fetch existing and keep the old token
    if config.get("token") == "********":
        existing = IntegrationService.get_project_integration(db, product_id, provider)
        if existing and "token" in existing:
            config["token"] = existing["token"]

    saved_config = IntegrationService.save_project_integration(db, product_id, provider, config)
    return {"message": "Configuração salva com sucesso", "provider": provider}

@router.delete("/project/{product_id}/{provider}/config")
def delete_project_integration_config(product_id: int, provider: str, db: Session = Depends(get_db)):
    IntegrationService.delete_project_integration(db, product_id, provider)
    return {"message": "Configuração removida com sucesso"}

@router.post("/project/{product_id}/{provider}/issue")
async def create_issue(product_id: int, provider: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    try:
        result = await IntegrationService.create_issue(db, product_id, provider, payload)
        return {"message": "Bug criado com sucesso", "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
