from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.product_service import ProductService
from app.schemas.product_schemas import ProductCreate, ProductResponse

router = APIRouter(
    prefix="/products",
    tags=["Products"],
    responses={404: {"description": "Produto não encontrado"}},
)

from app.auth import get_current_user
from app.models.user_models import UserDB

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
