import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routes.api_test_history_routes import router as history_router
from app.routes.auth_routes import router as auth_router
from app.routes.environment_routes import router as environment_router
from app.routes.flow_routes import router as flow_router
from app.routes.feature_routes import router as feature_router
from app.routes.product_routes import router as product_router
from app.routes.variable_routes import router as variable_router
from app.utils.logger import setup_logging
from app.models.feature_models import FeatureModel
from app.models.environment_model import Environment
from app.database import SessionLocal

# ===== CONFIGURAÇÃO DE LOGGING =====
setup_logging()

# Criar logger para este módulo
logger = logging.getLogger(__name__)
logger.info("=== INICIANDO API QA-WORKFLOW ===")

# Reset e Criação das tabelas
logger.info("Recriando tabelas do banco de dados...")
try:
    Base.metadata.drop_all(bind=engine)
    logger.info("Tabelas removidas com sucesso")
except Exception as e:
    logger.error(f"Erro ao remover tabelas: {e}")

try:
    Base.metadata.create_all(bind=engine)
    logger.info("Tabelas criadas com sucesso")
except Exception as e:
    logger.error(f"Erro ao criar tabelas: {e}")

# Seed Database removed as per user request
# def seed_database(): ... (removed)

logger.info("Inicializando aplicação FastAPI...")
app = FastAPI(
    title="QA WorkFlow API",
    description="API para gerenciamento de fluxos de trabalho de QA",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configuração CORS
logger.info("Configurando CORS middleware...")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produção, substitua por origens específicas
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuração Gzip
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

from app.routes.product_routes import router as product_router

# ... (Logging config kept in same order, inserting imports above or router below)
# Easier to just insert the router include call 

# Incluir rotas
logger.info("Registrando rotas...")
app.include_router(auth_router)
logger.info("Rota de autenticação registrada: /auth")
app.include_router(product_router)
logger.info("Rota de produtos registrada: /products")
app.include_router(flow_router)
logger.info("Rota de fluxo registrada: /flow")
app.include_router(feature_router)
logger.info("Rota de funcionalidades registrada: /features")
app.include_router(history_router)
logger.info("Rota de histórico registrada: /history")

app.include_router(environment_router)
logger.info("Rota de ambientes registrada: /environments")
app.include_router(variable_router)
logger.info("Rota de variáveis registrada: /variables")


# Rota de verificação de saúde (Health Check)
@app.get("/", tags=["Root"])
def read_root():
    logger.info("Health check solicitado")
    return {
        "status": "online",
        "message": "API de Fluxo rodando corretamente",
        "docs": "/docs",
        "version": "1.0.0",
    }


# Rota para verificar status das rotas
@app.get("/status", tags=["Status"])
def api_status():
    routes = []
    for route in app.routes:
        routes.append(
            {
                "path": route.path,
                "name": route.name,
                "methods": list(route.methods) if hasattr(route, "methods") else [],
            }
        )

    logger.info("Status da API solicitado")
    return {"status": "online", "routes": routes, "total_routes": len(routes)}


# Middleware para log de todas as requisições
@app.middleware("http")
async def log_requests(request, call_next):
    logger.info(f"REQUEST: {request.method} {request.url}")

    # Log dos headers (exceto Authorization por segurança)
    headers = dict(request.headers)
    if "authorization" in headers:
        headers["authorization"] = "***REDACTED***"

    logger.debug(f"Headers: {headers}")

    try:
        response = await call_next(request)
        logger.info(
            f"RESPONSE: {request.method} {request.url} - Status: {response.status_code}"
        )
        return response
    except Exception as e:
        logger.error(f"ERROR in {request.method} {request.url}: {str(e)}")
        raise


if __name__ == "__main__":
    logger.info("=== INICIANDO SERVIDOR UVICORN ===")
    logger.info("Servidor disponível em: http://127.0.0.1:8000")
    logger.info("Documentação: http://127.0.0.1:8000/docs")
    logger.info("Logs serão salvos em: api.log")

    import uvicorn

    # Executa o servidor se o arquivo for chamado diretamente
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=8000, reload=True, log_level="info"
    )
