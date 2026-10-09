from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from jose import jwt

from app.database import get_db
from app.models.team_models import TeamDB
from app.models.mock_models import ServiceMockDB
from app.models.company_models import CompanyDB
from app.models.user_models import UserDB
from app.auth import get_current_user
from app.routes.mock_routes import generate_unique_mock_slug

router = APIRouter()


@router.get("/v1/teams")
@router.get("/teams")
def list_teams(db: Session = Depends(get_db), current_user: Optional[UserDB] = Depends(get_current_user)):
    """Lista todos os times (squads de trabalho)."""
    query = db.query(TeamDB)
    if current_user and current_user.company_id:
        query = query.filter(
            (TeamDB.company_id == current_user.company_id) | (TeamDB.company_id == None)
        )
    teams = query.order_by(TeamDB.name.asc()).all()

    result = []
    for t in teams:
        result.append({
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "company_id": t.company_id,
            "mock_count": len(t.mocks) if t.mocks else 0,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None
        })
    return result


@router.post("/v1/teams")
@router.post("/teams")
def create_team(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db), current_user: Optional[UserDB] = Depends(get_current_user)):
    """Cria um novo time de trabalho / squad."""
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="O nome do time é obrigatório.")

    company_id = current_user.company_id if current_user else None

    team = TeamDB(
        name=name,
        description=payload.get("description", "").strip(),
        company_id=company_id
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "company_id": team.company_id,
        "mock_count": 0,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "updated_at": team.updated_at.isoformat() if team.updated_at else None
    }


@router.get("/v1/teams/{team_id}")
@router.get("/teams/{team_id}")
def get_team(team_id: int, db: Session = Depends(get_db)):
    """Obtém detalhes de um time específico."""
    team = db.query(TeamDB).filter(TeamDB.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Time não encontrado.")

    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "company_id": team.company_id,
        "mock_count": len(team.mocks) if team.mocks else 0,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "updated_at": team.updated_at.isoformat() if team.updated_at else None
    }


@router.put("/v1/teams/{team_id}")
@router.put("/teams/{team_id}")
@router.patch("/v1/teams/{team_id}")
@router.patch("/teams/{team_id}")
def update_team(team_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Atualiza as informações de um time."""
    team = db.query(TeamDB).filter(TeamDB.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Time não encontrado.")

    if "name" in payload and payload["name"].strip():
        team.name = payload["name"].strip()
    if "description" in payload:
        team.description = payload["description"].strip()

    db.commit()
    db.refresh(team)

    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "company_id": team.company_id,
        "mock_count": len(team.mocks) if team.mocks else 0,
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "updated_at": team.updated_at.isoformat() if team.updated_at else None
    }


@router.delete("/v1/teams/{team_id}")
@router.delete("/teams/{team_id}")
def delete_team(team_id: int, db: Session = Depends(get_db)):
    """Exclui um time e seus mocks associados."""
    team = db.query(TeamDB).filter(TeamDB.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Time não encontrado.")

    db.delete(team)
    db.commit()
    return {"message": "Time excluído com sucesso.", "id": team_id}


# ==============================================================================
# TEAM MOCKS
# ==============================================================================

@router.get("/v1/teams/{team_id}/mocks")
@router.get("/teams/{team_id}/mocks")
def list_team_mocks(team_id: int, db: Session = Depends(get_db)):
    """Lista todos os mocks pertencentes a um time específico."""
    team = db.query(TeamDB).filter(TeamDB.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Time não encontrado.")

    mocks = db.query(ServiceMockDB).filter(ServiceMockDB.team_id == team_id).all()
    result = []
    for m in mocks:
        result.append({
            "id": m.id,
            "team_id": m.team_id,
            "product_id": m.product_id,
            "name": m.name,
            "slug": m.slug,
            "description": m.description,
            "is_active": m.is_active,
            "target_url": m.target_url,
            "enable_proxy": m.enable_proxy,
            "force_real_api": m.force_real_api,
            "real_first_fallback_mock": m.real_first_fallback_mock,
            "rule_count": len(m.rules) if m.rules else 0,
            "created_at": m.created_at.isoformat() if m.created_at else None
        })
    return result


@router.post("/v1/teams/{team_id}/mocks")
@router.post("/teams/{team_id}/mocks")
def create_team_mock(team_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db), current_user: Optional[UserDB] = Depends(get_current_user)):
    """Cria um novo servidor mock associado ao time."""
    team = db.query(TeamDB).filter(TeamDB.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Time não encontrado.")

    # Validação de plano/limite de mocks se houver empresa
    if current_user and current_user.company_id:
        company = db.query(CompanyDB).filter(CompanyDB.id == current_user.company_id).first()
        max_mocks = 2
        if company and company.license_key:
            try:
                with open("public_key.pem", "rb") as key_file:
                    pub_key = key_file.read()
                token_payload = jwt.decode(company.license_key, pub_key, algorithms=["RS256"])
                max_mocks = token_payload.get("max_mocks", 2)
                if token_payload.get("plan") == "enterprise":
                    max_mocks = 999
            except Exception:
                pass

        current_mocks_count = db.query(ServiceMockDB).join(TeamDB, ServiceMockDB.team_id == TeamDB.id).filter(TeamDB.company_id == current_user.company_id).count()
        if current_mocks_count >= max_mocks:
            raise HTTPException(status_code=402, detail="Mock limit reached for your current plan. Please upgrade to Enterprise.")

    name = payload.get("name", "Novo Mock").strip()
    user_slug = payload.get("slug", "").strip()
    base_text = user_slug if user_slug else f"mock-{name}"
    final_slug = generate_unique_mock_slug(db, base_text)

    mock = ServiceMockDB(
        team_id=team_id,
        product_id=payload.get("product_id"),
        name=name,
        slug=final_slug,
        description=payload.get("description", ""),
        is_active=payload.get("is_active", True),
        target_url=payload.get("target_url"),
        enable_proxy=payload.get("enable_proxy", False),
        force_real_api=payload.get("force_real_api", False),
        real_first_fallback_mock=payload.get("real_first_fallback_mock", False)
    )
    db.add(mock)
    db.commit()
    db.refresh(mock)

    return {
        "id": mock.id,
        "team_id": mock.team_id,
        "product_id": mock.product_id,
        "name": mock.name,
        "slug": mock.slug,
        "is_active": mock.is_active,
        "target_url": mock.target_url,
        "enable_proxy": mock.enable_proxy,
        "force_real_api": mock.force_real_api,
        "real_first_fallback_mock": mock.real_first_fallback_mock,
        "rule_count": 0,
        "created_at": mock.created_at.isoformat() if mock.created_at else None
    }
