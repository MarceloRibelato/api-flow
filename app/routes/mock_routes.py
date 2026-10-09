import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Body, Form, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.mock_models import ServiceMockDB, MockRuleDB, MockLogDB
from app.models.company_models import CompanyDB
from app.models.user_models import UserDB
from app.auth import get_current_user
from jose import jwt
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
    result = []
    for m in mocks:
        result.append({
            "id": m.id,
            "product_id": m.product_id,
            "name": m.name,
            "slug": m.slug,
            "description": m.description,
            "is_active": m.is_active,
            "target_url": m.target_url,
            "enable_proxy": m.enable_proxy,
            "rule_count": len(m.rules),
            "created_at": m.created_at.isoformat() if m.created_at else None
        })
    return result


@router.post("/v1/projects/{project_id}/mocks")
@router.post("/projects/{project_id}/mocks")
def create_mock_server(project_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Cria um novo servidor de mock no projeto."""
    company = db.query(CompanyDB).filter(CompanyDB.id == current_user.company_id).first()
    max_mocks = 2 # Basic default
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

    current_mocks_count = db.query(ServiceMockDB).join(ProductModel, ServiceMockDB.product_id == ProductModel.id).filter(ProductModel.company_id == current_user.company_id).count()
    if current_mocks_count >= max_mocks:
        raise HTTPException(status_code=402, detail="Mock limit reached for your current plan. Please upgrade to Enterprise.")

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
        is_active=payload.get("is_active", True),
        target_url=payload.get("target_url"),
        enable_proxy=payload.get("enable_proxy", False)
    )
    db.add(mock)
    db.commit()
    db.refresh(mock)
    return {
        "id": mock.id,
        "name": mock.name,
        "slug": mock.slug,
        "is_active": mock.is_active,
        "target_url": mock.target_url,
        "enable_proxy": mock.enable_proxy,
        "force_real_api": mock.force_real_api,
        "real_first_fallback_mock": mock.real_first_fallback_mock
    }


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
        "team_id": mock.team_id,
        "product_id": mock.product_id,
        "name": mock.name,
        "slug": mock.slug,
        "description": mock.description,
        "is_active": mock.is_active,
        "target_url": mock.target_url,
        "enable_proxy": mock.enable_proxy,
        "force_real_api": mock.force_real_api,
        "real_first_fallback_mock": mock.real_first_fallback_mock,
        "rules": rules
    }


@router.put("/v1/mocks/{mock_id}")
@router.put("/mocks/{mock_id}")
@router.patch("/v1/mocks/{mock_id}")
@router.patch("/mocks/{mock_id}")
def update_mock_server(mock_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Atualiza as propriedades do servidor mock (incluindo proxy e target_url)."""
    mock = db.query(ServiceMockDB).filter(ServiceMockDB.id == mock_id).first()
    if not mock:
        raise HTTPException(status_code=404, detail="Mock Server não encontrado.")

    if "team_id" in payload:
        mock.team_id = payload["team_id"]
    if "name" in payload:
        mock.name = payload["name"]
    if "description" in payload:
        mock.description = payload["description"]
    if "is_active" in payload:
        mock.is_active = payload["is_active"]
    if "target_url" in payload:
        mock.target_url = payload["target_url"]
    if "enable_proxy" in payload:
        mock.enable_proxy = bool(payload["enable_proxy"])
        mock.force_real_api = bool(payload.get("force_real_api", False))
        mock.real_first_fallback_mock = bool(payload.get("real_first_fallback_mock", False))
    if "slug" in payload:
        # Prevent empty slug
        new_slug = payload["slug"].strip().lower()
        new_slug = re.sub(r'[^a-z0-9-_]', '-', new_slug)
        if new_slug:
            # Check for uniqueness
            from sqlalchemy import or_
            existing = db.query(ServiceMockDB).filter(ServiceMockDB.slug == new_slug, ServiceMockDB.id != mock_id).first()
            if existing:
                raise HTTPException(status_code=400, detail="Slug already in use")
            mock.slug = new_slug

    db.commit()
    db.refresh(mock)
    return {
        "id": mock.id,
        "product_id": mock.product_id,
        "name": mock.name,
        "slug": mock.slug,
        "description": mock.description,
        "is_active": mock.is_active,
        "target_url": mock.target_url,
        "enable_proxy": mock.enable_proxy,
        "force_real_api": mock.force_real_api,
        "real_first_fallback_mock": mock.real_first_fallback_mock
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

@router.post("/v1/teams/{team_id}/mocks/import")
@router.post("/teams/{team_id}/mocks/import")
@router.post("/v1/projects/{project_id}/mocks/import")
@router.post("/projects/{project_id}/mocks/import")
async def import_mock_spec(
    request: Request,
    project_id: Optional[int] = None,
    team_id: Optional[int] = None,
    team_id_form: Optional[int] = Form(None, alias="team_id"),
    import_type: Optional[str] = Form(None),
    mock_name: Optional[str] = Form(None),
    mock_id: Optional[int] = Form(None),
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
            mock_id = body_json.get("mock_id") or mock_id
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
    errors = []

    # Detectar formato provável no texto
    stripped = raw_text.strip()
    is_curl = stripped.startswith("curl ") or stripped.startswith("curl\n") or stripped.startswith("curl\r")
    is_swagger = "swagger:" in stripped[:250] or "openapi:" in stripped[:250] or '"swagger"' in stripped[:250] or '"openapi"' in stripped[:250] or "\npaths:" in stripped or '"paths":' in stripped
    is_asyncapi = "asyncapi:" in stripped[:250] or '"asyncapi"' in stripped[:250] or "\nchannels:" in stripped or '"channels":' in stripped
    is_postman = '"_postman_id"' in stripped or ('"item"' in stripped and '"info"' in stripped)

    detected_type = import_type
    if is_swagger or is_asyncapi:
        detected_type = "swagger"
    elif is_postman:
        detected_type = "postman"
    elif is_curl:
        detected_type = "curl"

    def try_parse_swagger_or_asyncapi(text):
        try:
            data = json.loads(text)
        except Exception:
            try:
                import yaml
            except ImportError:
                import sys
                sys.path.insert(0, '/app')
                import yaml
            data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("O conteúdo não é um dicionário Swagger/OpenAPI/AsyncAPI válido.")
        
        if "channels" in data or "asyncapi" in data:
            return MockImportService.parse_asyncapi_spec(data)
        return MockImportService.parse_swagger_spec(data)

    def try_parse_postman(text):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("O conteúdo não é uma coleção Postman válida.")
        return MockImportService.parse_postman_collection(data)

    def try_parse_curl(text):
        return MockImportService.parse_curl_command(text)

    # Ordem de tentativa: tipo detectado/solicitado primeiro, depois fallbacks
    attempts = [detected_type]
    for fallback in ["swagger", "postman", "curl"]:
        if fallback not in attempts:
            attempts.append(fallback)

    for attempt in attempts:
        try:
            if attempt == "swagger":
                rules = try_parse_swagger_or_asyncapi(raw_text)
            elif attempt == "postman":
                rules = try_parse_postman(raw_text)
            elif attempt == "curl":
                rules = try_parse_curl(raw_text)
            else:
                rules = []

            if rules:
                parsed_rules = rules
                import_type = attempt
                break
        except Exception as e:
            errors.append(f"[{attempt}]: {str(e)}")

    if not parsed_rules:
        detail_msg = f"Nenhum endpoint válido foi identificado. Erros: {'; '.join(errors)}" if errors else "Nenhum endpoint válido foi identificado no arquivo enviado."
        raise HTTPException(status_code=400, detail=detail_msg)

    # Garantir que project_id corresponda a um produto válido no banco
    product = db.query(ProductModel).filter(ProductModel.id == project_id).first()
    if not product:
        product = db.query(ProductModel).first()
        if not product:
            product = ProductModel(name="Projeto Principal", description="Projeto Padrão")
            db.add(product)
            db.flush()
        project_id = product.id

    try:
        mock = None
        if mock_id:
            mock = db.query(ServiceMockDB).filter(ServiceMockDB.id == mock_id).first()
        
        is_update = bool(mock)

        if not is_update:
            # Determinar team_id e product_id
            target_team_id = team_id or team_id_form
            target_product_id = project_id
            if target_team_id and not target_product_id:
                target_product_id = target_team_id

            # Criar Servidor Mock
            name = mock_name or f"Mock {import_type.upper()} ({len(parsed_rules) // 4 if len(parsed_rules) >= 4 else 1} Endpoints)"
            final_slug = generate_unique_mock_slug(db, f"mock-{import_type}")
            mock = ServiceMockDB(
                team_id=target_team_id,
                product_id=target_product_id,
                name=name,
                slug=final_slug,
                description=f"Importado via {import_type.upper()} com respostas automáticas para 200, 400, 401 e 500.",
                is_active=True
            )
            db.add(mock)
            db.flush()

        # Obter regras existentes caso seja update
        existing_signatures = set()
        if is_update:
            existing_rules = db.query(MockRuleDB).filter(MockRuleDB.mock_id == mock.id).all()
            for er in existing_rules:
                # Assinatura: method + path + status
                existing_signatures.add(f"{er.method}_{er.path_pattern}_{er.response_status}")

        # Adicionar regras geradas
        created_rule_count = 0
        endpoints_added = set()
        
        for r in parsed_rules:
            sig = f"{r['method']}_{r['path_pattern']}_{r['response_status']}"
            if is_update and sig in existing_signatures:
                continue
                
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
            endpoints_added.add(f"{r['method']}_{r['path_pattern']}")

        db.commit()
        db.refresh(mock)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Erro ao salvar mock e regras no banco de dados: {str(e)}")

    if is_update:
        if created_rule_count > 0:
            msg = f"Mock atualizado com sucesso! Foram adicionadas {len(endpoints_added)} novas APIs ({created_rule_count} regras/variações) que não existiam."
        else:
            msg = "Nenhuma nova API foi adicionada. Todos os endpoints do arquivo já existiam no Mock Server."
        return {
            "message": msg,
            "user_guidance": msg,
            "mock_id": mock.id,
            "mock_slug": mock.slug,
            "endpoints_added": len(endpoints_added),
            "rules_created": created_rule_count
        }
    else:
        return {
            "message": "Importação concluída com sucesso!",
            "user_guidance": f"Foram importadas {len(endpoints_added)} APIs e geradas {created_rule_count} respostas mockadas automáticas seguindo boas práticas. Recomendamos que você revise e ajuste o corpo dos retornos para atender às regras do seu negócio.",
            "mock_id": mock.id,
            "mock_slug": mock.slug,
            "endpoints_added": len(endpoints_added),
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
            "is_proxied": getattr(log, "is_proxied", False),
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
