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

@router.get("/", response_model=List[ProductResponse])
def list_products(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Listar todos os produtos.
    """
    return ProductService.list(db, skip=skip, limit=limit)

@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db)
):
    """
    Criar um novo produto.
    """
    return ProductService.create(db, product)

@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: int,
    db: Session = Depends(get_db)
):
    """
    Obter detalhes de um produto específico.
    """
    db_product = ProductService.get(db, product_id)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return db_product

@router.put("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    product: ProductCreate,
    db: Session = Depends(get_db)
):
    """
    Atualizar um produto existente.
    """
    db_product = ProductService.update(db, product_id, product)
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return db_product

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db)
):
    """
    Remover um produto.
    """
    success = ProductService.delete(db, product_id)
    if not success:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return 
