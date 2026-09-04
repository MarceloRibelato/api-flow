from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.feature_schemas import FeatureCreate, FeatureResponse, ReorderSchema
from app.services.feature_service import FeatureService

from app.services.audit_service import AuditService
from ..auth import get_current_user
from ..models.user_models import UserDB

router = APIRouter(prefix="/features", tags=["Features"])


@router.post("/", response_model=FeatureResponse)
def create_feature(
    feature: FeatureCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if current_user.role == 'viewer':
        raise HTTPException(status_code=403, detail="Sem permissão")
        
    created = FeatureService.create(db, feature, company_id=current_user.company_id)
    if not created:
         raise HTTPException(status_code=403, detail="Produto não pertence à sua empresa")

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="CREATE_FEATURE",
        resource_type="feature",
        resource_id=str(created.id),
        resource_name=created.name,
        details={
            "feature_id": created.id,
            "feature_name": created.name,
            "product_id": created.product_id,
            "project_id": created.id
        }
    )
    return created


@router.put("/reorder")
def reorder_features(
    data: ReorderSchema,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão")

    success = FeatureService.reorder(db, data, company_id=current_user.company_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reorder features")

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="REORDER_FEATURES",
        resource_type="feature",
        details={"reordered_count": len(data.new_order) if data.new_order else 0}
    )
    return {"status": "success"}


@router.get("/", response_model=List[FeatureResponse])
def read_features(
    product_id: int = None,
    db: Session = Depends(get_db), 
    current_user: UserDB = Depends(get_current_user)
):
    return FeatureService.get_all(db, product_id=product_id, company_id=current_user.company_id)


@router.get("/{feature_id}", response_model=FeatureResponse)
def read_feature(
    feature_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    feature = FeatureService.get_by_id(db, feature_id, company_id=current_user.company_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Funcionalidade não encontrada")
    return feature


@router.put("/{feature_id}", response_model=FeatureResponse)
def update_feature(
    feature_id: int,
    feature_update: FeatureCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão")

    updated_feature = FeatureService.update(db, feature_id, feature_update, company_id=current_user.company_id)
    if not updated_feature:
        raise HTTPException(status_code=404, detail="Funcionalidade não encontrada")

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="UPDATE_FEATURE",
        resource_type="feature",
        resource_id=str(updated_feature.id),
        resource_name=updated_feature.name,
        details={
            "feature_id": updated_feature.id,
            "feature_name": updated_feature.name,
            "product_id": updated_feature.product_id,
            "project_id": updated_feature.id
        }
    )
    return updated_feature


@router.delete("/{feature_id}")
def delete_feature(
    feature_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão")

    feat = FeatureService.get_by_id(db, feature_id, company_id=current_user.company_id)
    feat_name = feat.name if feat else str(feature_id)
    prod_id = feat.product_id if feat else None

    success = FeatureService.delete(db, feature_id, company_id=current_user.company_id)
    if not success:
        raise HTTPException(status_code=404, detail="Funcionalidade não encontrada")

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="DELETE_FEATURE",
        resource_type="feature",
        resource_id=str(feature_id),
        resource_name=feat_name,
        details={
            "feature_id": feature_id,
            "feature_name": feat_name,
            "product_id": prod_id,
            "project_id": feature_id
        }
    )
    return {"message": f"Funcionalidade {feature_id} removida com sucesso"}
