from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.product_service import ProductService
from app.schemas.product_schemas import ProductCreate, ProductResponse, ProductMobileSettingsCreate, ProductMobileSettingsResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter(
    prefix="/products",
    tags=["Products"],
    responses={404: {"description": "Produto não encontrado"}},
)

from app.auth import get_current_user
from app.models.user_models import UserDB

# --- Schemas inline for Device Matrix ---
class MobileDeviceCreate(BaseModel):
    label: str
    device_name: str
    platform: str = "android"
    platform_version: Optional[str] = None
    is_active: bool = True
    provider_override: Optional[str] = None
    server_url: Optional[str] = None
    auth_user: Optional[str] = None
    auth_token: Optional[str] = None
    app_identifier: Optional[str] = None

class MobileDeviceResponse(MobileDeviceCreate):
    id: int
    product_id: int
    class Config:
        from_attributes = True

# ... (router def)

@router.get("/", response_model=List[ProductResponse])
def list_products(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Listar todos os produtos da empresa do usuário.
    """
    return ProductService.list(db, company_id=current_user.company_id, skip=skip, limit=limit)

@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Criar um novo produto para a empresa.
    """
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão para criar produtos")
         
    return ProductService.create(db, product, company_id=current_user.company_id)

@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Obter detalhes de um produto específico.
    """
    db_product = ProductService.get(db, product_id, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return db_product

@router.put("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    product: ProductCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Atualizar um produto existente.
    """
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão para editar")

    db_product = ProductService.update(db, product_id, product, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return db_product

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Remover um produto.
    """
    if current_user.role != 'admin':
         raise HTTPException(status_code=403, detail="Apenas administradores podem deletar produtos")

    success = ProductService.delete(db, product_id, company_id=current_user.company_id)
    if not success:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return 

@router.get("/{product_id}/mobile-settings", response_model=ProductMobileSettingsResponse)
def get_product_mobile_settings(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Obter as configurações de mobile do produto.
    """
    db_product = ProductService.get(db, product_id, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    
    return ProductService.get_mobile_settings(db, product_id)

@router.put("/{product_id}/mobile-settings", response_model=ProductMobileSettingsResponse)
def update_product_mobile_settings(
    product_id: int,
    settings_data: ProductMobileSettingsCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Atualizar as configurações de mobile do produto.
    """
    if current_user.role == 'viewer':
         raise HTTPException(status_code=403, detail="Sem permissão para editar")

    db_product = ProductService.get(db, product_id, company_id=current_user.company_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    return ProductService.update_mobile_settings(db, product_id, settings_data)

# ─────────────────────────────────────────────────────────
# Device Matrix Routes
# ─────────────────────────────────────────────────────────

@router.get("/{product_id}/mobile-devices", response_model=List[MobileDeviceResponse])
def list_mobile_devices(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Listar dispositivos da matriz de execução mobile."""
    from app.models.product_models import ProductMobileDeviceDB
    return db.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.product_id == product_id
    ).all()

@router.post("/{product_id}/mobile-devices", response_model=MobileDeviceResponse, status_code=status.HTTP_201_CREATED)
def create_mobile_device(
    product_id: int,
    device: MobileDeviceCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Adicionar dispositivo à matriz."""
    if current_user.role == 'viewer':
        raise HTTPException(status_code=403, detail="Sem permissão")
    from app.models.product_models import ProductMobileDeviceDB
    db_device = ProductMobileDeviceDB(product_id=product_id, **device.dict())
    db.add(db_device)
    db.commit()
    db.refresh(db_device)
    return db_device

@router.put("/{product_id}/mobile-devices/{device_id}", response_model=MobileDeviceResponse)
def update_mobile_device(
    product_id: int,
    device_id: int,
    device: MobileDeviceCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Atualizar um dispositivo da matriz."""
    if current_user.role == 'viewer':
        raise HTTPException(status_code=403, detail="Sem permissão")
    from app.models.product_models import ProductMobileDeviceDB
    db_device = db.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.id == device_id,
        ProductMobileDeviceDB.product_id == product_id
    ).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    for k, v in device.dict().items():
        setattr(db_device, k, v)
    db.commit()
    db.refresh(db_device)
    return db_device

@router.delete("/{product_id}/mobile-devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mobile_device(
    product_id: int,
    device_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Remover dispositivo da matriz."""
    if current_user.role == 'viewer':
        raise HTTPException(status_code=403, detail="Sem permissão")
    from app.models.product_models import ProductMobileDeviceDB
    db_device = db.query(ProductMobileDeviceDB).filter(
        ProductMobileDeviceDB.id == device_id,
        ProductMobileDeviceDB.product_id == product_id
    ).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    db.delete(db_device)
    db.commit()
    return
