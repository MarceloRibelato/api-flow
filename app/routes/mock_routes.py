import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Body, Form, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.mock_models import ServiceMockDB, MockRuleDB, MockLogDB
from app.models.product_models import ProductModel
from app.services.mock_service import MockExecutionEngine
from app.services.mock_import_service import MockImportService

router = APIRouter()


import re

def generate_unique_mock_slug(db: Session, base_text: str) -> str:
    """Gera um slug único no banco de dados para o Servidor Mock."""
    cleaned = re.sub(r'[^a-z0-9\-]', '', base_text.lower().replace(" ", "-"))
    if not cleaned:
        cleaned = "mock"

    candidate = cleaned
    counter = 1
    while db.query(ServiceMockDB).filter(ServiceMockDB.slug == candidate).first() is not None:
        counter += 1
        candidate = f"{cleaned}-{counter}"
    return candidate


# ==============================================================================
# CRUD - MOCK SERVERS
# ==============================================================================

@router.get("/v1/projects/{project_id}/mocks")
@router.get("/projects/{project_id}/mocks")
def list_project_mocks(project_id: int, db: Session = Depends(get_db)):
    """Lista todos os servidores mock de um determinado projeto."""
    mocks = db.query(ServiceMockDB).filter(ServiceMockDB.product_id == project_id).all()
    if not mocks:
        # Se não houver mocks para este project_id específico, retornar todos os mocks cadastrados no sistema como fallback
        mocks = db.query(ServiceMockDB).all()
    result = []
    for m in mocks:
        result.append({
            "id": m.id,
            "product_id": m.product_id,
            "name": m.name,
            "slug": m.slug,
            "description": m.description,
            "is_active": m.is_active,
            "rule_count": len(m.rules),
            "created_at": m.created_at.isoformat() if m.created_at else None
        })
    return result


@router.post("/v1/projects/{project_id}/mocks")
@router.post("/projects/{project_id}/mocks")
def create_mock_server(project_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Cria um novo servidor de mock no projeto."""
    # Garantir que project_id é um produto existente
    product = db.query(ProductModel).filter(ProductModel.id == project_id).first()
    if not product:
        product = db.query(ProductModel).first()
        if not product:
            product = ProductModel(name="Projeto Principal", description="Projeto Padrão")
            db.add(product)
            db.flush()
        project_id = product.id

    name = payload.get("name", "Novo Mock").strip()
    user_slug = payload.get("slug", "").strip()
    base_text = user_slug if user_slug else f"mock-{name}"
    final_slug = generate_unique_mock_slug(db, base_text)

    mock = ServiceMockDB(
        product_id=project_id,
        name=name,
        slug=final_slug,
        description=payload.get("description", ""),
        is_active=payload.get("is_active", True)
    )
    db.add(mock)
    db.commit()
    db.refresh(mock)
    return {"id": mock.id, "name": mock.name, "slug": mock.slug, "is_active": mock.is_active}


@router.get("/v1/mocks/{mock_id}")
@router.get("/mocks/{mock_id}")
def get_mock_details(mock_id: int, db: Session = Depends(get_db)):
    """Obtém detalhes do servidor mock com todas as suas regras cadastradas."""
    mock = db.query(ServiceMockDB).filter(ServiceMockDB.id == mock_id).first()
    if not mock:
        raise HTTPException(status_code=404, detail="Mock Server não encontrado.")

    rules = []
    for r in mock.rules:
        rules.append({
            "id": r.id,
            "name": r.name,
            "method": r.method,
            "path_pattern": r.path_pattern,
            "priority": r.priority,
            "match_query_params": r.match_query_params,
            "match_headers": r.match_headers,
            "match_body_pattern": r.match_body_pattern,
            "response_status": r.response_status,
            "response_headers": r.response_headers,
            "response_body": r.response_body,
            "delay_ms": r.delay_ms,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })

    return {
        "id": mock.id,
        "product_id": mock.product_id,
        "name": mock.name,
        "slug": mock.slug,
        "description": mock.description,
        "is_active": mock.is_active,
        "rules": rules
    }


@router.delete("/v1/mocks/{mock_id}")
@router.delete("/mocks/{mock_id}")
def delete_mock_server(mock_id: int, db: Session = Depends(get_db)):
    """Remove um servidor de mock e todas as suas regras/logs."""
    mock = db.query(ServiceMockDB).filter(ServiceMockDB.id == mock_id).first()
    if not mock:
        raise HTTPException(status_code=404, detail="Mock Server não encontrado.")
    db.delete(mock)
    db.commit()
    return {"message": "Servidor de mock removido com sucesso."}


# ==============================================================================
# IMPORTAÇÃO DE REGRAS (Postman, Swagger, cURL)
# ==============================================================================

@router.post("/v1/projects/{project_id}/mocks/import")
@router.post("/projects/{project_id}/mocks/import")
async def import_mock_spec(
    project_id: int,
    request: Request,
    import_type: Optional[str] = Form(None),
    mock_name: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """
    Importa especificações de Postman, Swagger/OpenAPI ou cURL, criando um Mock Server
    e gerando automaticamente variações de boas práticas para os status 200, 400, 401 e 500.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body_json = await request.json()
            import_type = body_json.get("import_type", "swagger")
            mock_name = body_json.get("mock_name")
            content = body_json.get("content")
        except Exception:
            pass

    import_type = import_type or "swagger"
    raw_text = content or ""
    if file:
        file_bytes = await file.read()
        raw_text = file_bytes.decode("utf-8")

    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="Conteúdo da especificação ausente.")

    parsed_rules = []
    if import_type == "postman":
        try:
            data = json.loads(raw_text)
            parsed_rules = MockImportService.parse_postman_collection(data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao ler JSON da coleção Postman: {e}")
    elif import_type == "swagger":
        try:
            try:
                data = json.loads(raw_text)
            except Exception:
                import yaml
                data = yaml.safe_load(raw_text)
            parsed_rules = MockImportService.parse_swagger_spec(data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao ler documentação Swagger (JSON ou YAML): {e}")
    elif import_type == "curl":
        parsed_rules = MockImportService.parse_curl_command(raw_text)
    else:
        raise HTTPException(status_code=400, detail="Tipo de importação inválido. Use postman, swagger ou curl.")

    if not parsed_rules:
        raise HTTPException(status_code=400, detail="Nenhum endpoint válido foi identificado no arquivo enviado.")

    # Garantir que project_id corresponda a um produto válido no banco
    product = db.query(ProductModel).filter(ProductModel.id == project_id).first()
    if not product:
        product = db.query(ProductModel).first()
        if not product:
            product = ProductModel(name="Projeto Principal", description="Projeto Padrão")
            db.add(product)
            db.flush()
        project_id = product.id

    # Criar Servidor Mock
    name = mock_name or f"Mock {import_type.upper()} ({len(parsed_rules) // 4 if len(parsed_rules) >= 4 else 1} Endpoints)"
    final_slug = generate_unique_mock_slug(db, f"mock-{import_type}")

    try:
        mock = ServiceMockDB(
            product_id=project_id,
            name=name,
            slug=final_slug,
            description=f"Importado via {import_type.upper()} com respostas automáticas para 200, 400, 401 e 500.",
            is_active=True
        )
        db.add(mock)
        db.flush()

        # Adicionar regras geradas
        created_rule_count = 0
        for r in parsed_rules:
            rule_db = MockRuleDB(
                mock_id=mock.id,
                name=r["name"],
                method=r["method"],
                path_pattern=r["path_pattern"],
                priority=r.get("priority", 1),
                match_query_params=r.get("match_query_params"),
                match_headers=r.get("match_headers"),
                match_body_pattern=r.get("match_body_pattern"),
                response_status=r["response_status"],
                response_headers=r.get("response_headers", {"Content-Type": "application/json"}),
                response_body=r.get("response_body", ""),
                delay_ms=r.get("delay_ms", 0),
                is_active=True
            )
            db.add(rule_db)
            created_rule_count += 1

        db.commit()
        db.refresh(mock)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Erro ao salvar mock e regras no banco de dados: {str(e)}")

    return {
        "message": "Importação concluída com sucesso!",
        "user_guidance": "Foram geradas respostas mockadas automáticas (status 200, 400, 401, 404, 500 e 504) seguindo boas práticas. Recomendamos que você revise e ajuste o corpo dos retornos para atender às regras do seu negócio.",
        "mock_id": mock.id,
        "mock_slug": mock.slug,
        "endpoints_count": len(parsed_rules) // 6 if len(parsed_rules) >= 6 else 1,
        "rules_created": created_rule_count
    }


# ==============================================================================
# GESTÃO DE REGRAS INDIVIDUAIS & LOGS
# ==============================================================================

@router.post("/v1/mocks/{mock_id}/rules")
@router.post("/mocks/{mock_id}/rules")
def create_mock_rule(mock_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Adiciona uma nova regra individual ao servidor mock."""
    mock = db.query(ServiceMockDB).filter(ServiceMockDB.id == mock_id).first()
    if not mock:
        raise HTTPException(status_code=404, detail="Mock Server não encontrado.")

    rule = MockRuleDB(
        mock_id=mock.id,
        name=payload.get("name", "Nova Regra"),
        method=payload.get("method", "GET").upper(),
        path_pattern=payload.get("path_pattern", "/"),
        priority=payload.get("priority", 1),
        match_query_params=payload.get("match_query_params"),
        match_headers=payload.get("match_headers"),
        match_body_pattern=payload.get("match_body_pattern"),
        response_status=payload.get("response_status", 200),
        response_headers=payload.get("response_headers", {"Content-Type": "application/json"}),
        response_body=payload.get("response_body", "{}"),
        delay_ms=payload.get("delay_ms", 0),
        is_active=payload.get("is_active", True)
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"id": rule.id, "name": rule.name, "response_status": rule.response_status}


@router.put("/v1/mocks/rules/{rule_id}")
@router.put("/mocks/rules/{rule_id}")
def update_mock_rule(rule_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Atualiza os campos de uma regra existente."""
    rule = db.query(MockRuleDB).filter(MockRuleDB.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra de mock não encontrada.")

    for field in ["name", "method", "path_pattern", "priority", "match_query_params", "match_headers", "match_body_pattern", "response_status", "response_headers", "response_body", "delay_ms", "is_active"]:
        if field in payload:
            setattr(rule, field, payload[field])

    db.commit()
    return {"message": "Regra atualizada com sucesso.", "id": rule.id}


@router.delete("/v1/mocks/rules/{rule_id}")
@router.delete("/mocks/rules/{rule_id}")
def delete_mock_rule(rule_id: int, db: Session = Depends(get_db)):
    """Remove uma regra de mock."""
    rule = db.query(MockRuleDB).filter(MockRuleDB.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    db.delete(rule)
    db.commit()
    return {"message": "Regra removida com sucesso."}


@router.get("/v1/mocks/{mock_id}/logs")
@router.get("/mocks/{mock_id}/logs")
def get_mock_logs(mock_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """Obtém o histórico de requisições recebidas pelo servidor de mock."""
    logs = db.query(MockLogDB).filter(MockLogDB.mock_id == mock_id).order_by(MockLogDB.executed_at.desc()).limit(limit).all()
    result = []
    for log in logs:
        result.append({
            "id": log.id,
            "rule_id": log.rule_id,
            "client_ip": log.client_ip,
            "method": log.method,
            "path": log.path,
            "request_headers": log.request_headers,
            "request_body": log.request_body,
            "response_status": log.response_status,
            "response_body": log.response_body,
            "executed_at": log.executed_at.isoformat() if log.executed_at else None
        })
    return result


# ==============================================================================
# ROTA CURINGA DE INTERCEPTAÇÃO UNIVERSAL
# ==============================================================================

@router.api_route("/v1/mock/{mock_slug}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@router.api_route("/mock/{mock_slug}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def handle_mock_gateway(mock_slug: str, path: str, request: Request, db: Session = Depends(get_db)):
    """
    Gateway Interceptador Universal de Mocks.
    Atende qualquer requisição feita para /mock/{mock_slug}/{path} ou /v1/mock/{mock_slug}/{path}
    e avalia as regras para retornar a resposta mockada com a latência e templating configurados.
    """
    mock = db.query(ServiceMockDB).filter(ServiceMockDB.slug == mock_slug, ServiceMockDB.is_active == True).first()
    if not mock:
        return Response(
            content=json.dumps({"error": "Mock Service Inactive or Not Found", "mock_slug": mock_slug}),
            status_code=404,
            media_type="application/json"
        )

    # Coletar dados da requisição
    method = request.method
    client_ip = request.client.host if request.client else "unknown"
    query_params = dict(request.query_params)
    headers = dict(request.headers)

    # Coletar Body como texto
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="ignore") if body_bytes else ""

    # Processar mock via Engine
    status_code, resp_headers, final_body = await MockExecutionEngine.process_mock_request(
        db=db,
        mock=mock,
        method=method,
        path=f"/{path}",
        headers=headers,
        query_params=query_params,
        body_text=body_text,
        client_ip=client_ip,
        raw_url=str(request.url)
    )

    return Response(
        content=final_body,
        status_code=status_code,
        headers=resp_headers,
        media_type=resp_headers.get("Content-Type", "application/json")
    )
