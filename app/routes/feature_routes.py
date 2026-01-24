from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.feature_schemas import FeatureCreate, FeatureResponse, ReorderSchema
from app.services.feature_service import FeatureService

from ..auth import get_current_user
from ..models.user_models import UserDB

router = APIRouter(prefix="/features", tags=["Features"])


@router.post("/", response_model=FeatureResponse)
def create_feature(
    feature: FeatureCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    return FeatureService.create(db, feature)


@router.put("/reorder")
def reorder_features(
    data: ReorderSchema,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    success = FeatureService.reorder(db, data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reorder features")
    return {"status": "success"}


@router.get("/", response_model=List[FeatureResponse])
def read_features(
    product_id: int = None,
    db: Session = Depends(get_db), 
    current_user: UserDB = Depends(get_current_user)
):
    return FeatureService.get_all(db, product_id)


@router.get("/{feature_id}", response_model=FeatureResponse)
def read_feature(
    feature_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    feature = FeatureService.get_by_id(db, feature_id)
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
    updated_feature = FeatureService.update(db, feature_id, feature_update)
    if not updated_feature:
        raise HTTPException(status_code=404, detail="Funcionalidade não encontrada")
    return updated_feature


@router.delete("/{feature_id}")
def delete_feature(
    feature_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    success = FeatureService.delete(db, feature_id)
    if not success:
        raise HTTPException(status_code=404, detail="Funcionalidade não encontrada")
    return {"message": f"Funcionalidade {feature_id} removida com sucesso"}
