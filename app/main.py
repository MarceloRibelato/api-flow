import logging
import os
from contextlib import asynccontextmanager

import httpx # Added for global client
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.config import settings
from app.database import Base, engine, SessionLocal
from app.utils.logger import setup_logging
from app.services.scheduler_service import scheduler_service

# Routes
from app.routes.admin_routes import router as admin_router
from app.routes.api_test_history_routes import router as history_router
from app.routes.auth_routes import router as auth_router
from app.routes.environment_routes import router as environment_router
from app.routes.flow_routes import router as flow_router
from app.routes.feature_routes import router as feature_router
from app.routes.product_routes import router as product_router
from app.routes.variable_routes import router as variable_router
from app.routes.schedule_routes import router as schedule_router
from app.routes.capture_routes import router as capture_router
from app.routes.analysis_routes import router as analysis_router
from app.routes.agent_routes import router as agent_router
from app.routes.execution_routes import router as execution_router
from app.routes.import_routes import router as import_router
from app.routes.cicd_routes import router as cicd_router
from app.routes.skill_routes import router as skill_router
from app.routes.company_routes import router as company_router
from app.routes.appium_inspector_routes import router as appium_inspector_router
from app.routes.web_inspector_routes import router as web_inspector_router
from app.routes.hitl_routes import router as hitl_router

# Ensure models are loaded for create_all
import app.models.hitl_models

# ===== CONFIGURAÇÃO DE LOGGING =====
setup_logging()
logger = logging.getLogger(__name__)

# ===== LIFESPAN MANAGER =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables and start scheduler
    logger.info("=== INICIANDO API QA-WORKFLOW ===")
    
    # Retry logic for DB connection
    import time
    from sqlalchemy.exc import OperationalError
    
    max_retries = 30
    retry_interval = 2
    
    for i in range(max_retries):
        try:
            logger.info(f"Tentativa de conexão com DB ({i+1}/{max_retries})...")
            # Step 1: Create missing tables via SQL Alchemy
            Base.metadata.create_all(bind=engine)
            logger.info("Tabelas verificadas/criadas via metadata sqlalchemy")
            
            # Step 1.5: Adicionar colunas novas que não foram criadas via metadata
            from sqlalchemy import text
            try:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE flow_card_data ADD COLUMN IF NOT EXISTS db_queries JSON DEFAULT '[]'::json;"))
            except Exception as e:
                logger.info(f"Erro db_queries: {e}")
            
            try:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE flow_card_data ADD COLUMN IF NOT EXISTS message_queues JSON DEFAULT '[]'::json;"))
            except Exception as e:
                logger.info(f"Erro message_queues: {e}")

            try:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE flow_edges ADD COLUMN IF NOT EXISTS label VARCHAR(255);"))
            except Exception as e:
                logger.info(f"Erro label: {e}")

            # Auto-repair for trigger_origin if alembic failed
            try:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE api_test_execution_history ADD COLUMN IF NOT EXISTS trigger_origin VARCHAR(50) DEFAULT 'manual';"))
                    conn.execute(text("ALTER TABLE api_test_execution_history_archive ADD COLUMN IF NOT EXISTS trigger_origin VARCHAR(50) DEFAULT 'manual';"))
                    
                    # Fix previously misclassified schedules (one-time manual schedules were saved as pipeline)
                    conn.execute(text("""
                        UPDATE api_test_execution_history
                        SET trigger_origin = 'schedule'
                        FROM schedules
                        WHERE api_test_execution_history.schedule_id = schedules.id
                        AND api_test_execution_history.trigger_origin = 'pipeline'
                        AND schedules.name NOT LIKE 'CI/CD%'
                        AND schedules.name NOT LIKE '[PIPELINE]%'
                    """))
            except Exception as e:
                logger.info(f"Erro trigger_origin: {e}")

            logger.info("Colunas dinâmicas processadas.")
            
            # CRITICAL: Dispose engine to release connections before Alembic takes over
            # This prevents deadlocks in standard PostgreSQL/SQLAlchemy pooling.
            # Skip this and Alembic migration if running with SQLite (tests) to preserve the in-memory database.
            if "sqlite" not in str(engine.url):
                engine.dispose()
                logger.debug("SQLAlchemy Engine disposed (migrations are now handled externally)")
            
            break
        except Exception as e:
            if i < max_retries - 1:
                logger.warning(f"DB ainda não disponível, aguardando {retry_interval}s... Erro: {e}")
                time.sleep(retry_interval)
            else:
                logger.error(f"Erro CRÍTICO ao conectar no DB após {max_retries} tentativas: {e}")
                raise e

    logger.info("Iniciando scheduler...")
    try:
        scheduler_service.start()
        db = SessionLocal()
        scheduler_service.sync_jobs(db)
        db.close()
    except Exception as e:
        logger.error(f"Erro ao iniciar scheduler: {e}")

    # Init Global HTTP Client (Persistent Connection Pool)
    # verify=False is often useful for local dev with self-signed certs, fitting the 'proxy' nature here
    app.state.http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True, verify=settings.VERIFY_SSL)
    logger.info("Global HTTP Client initialized")

    # License check
    if settings.LICENSE_MANAGER_URL and settings.QA_FLOW_LICENSE_KEY:
        logger.info("=== VERIFICANDO LICENÇA DO QA-FLOW ===")
        try:
            import httpx as sync_httpx
            with sync_httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{settings.LICENSE_MANAGER_URL.rstrip('/')}/api/licenses/validate",
                    json={"license_key": settings.QA_FLOW_LICENSE_KEY}
                )
                if resp.status_code == 200:
                    lic_info = resp.json()
                    if lic_info.get("valid"):
                        logger.info(f"✅ Licença Válida! Registrada para: {lic_info.get('client_name')}. Expira em: {lic_info.get('expires_at')}")
                    else:
                        logger.warning(f"❌ Licença Inválida ou Expirada! Mensagem: {lic_info.get('message')}")
                else:
                    logger.warning(f"⚠️ Falha ao validar licença no servidor: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Não foi possível conectar ao Gerenciador de Licenças: {e}")

    yield
    
    # Shutdown
    logger.info("Desligando aplicação...")
    await app.state.http_client.aclose()
    logger.info("Global HTTP Client closed")

    if scheduler_service.scheduler.running:
        scheduler_service.scheduler.shutdown()

# ===== APP INIT =====
app = FastAPI(
    title="QA WorkFlow API",
    description="API para gerenciamento de fluxos de trabalho de QA",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    root_path="/api" if "db" in settings.DATABASE_URL or os.path.exists("/.dockerenv") else "",
    lifespan=lifespan
)

# Configuração Middlewares
allowed_origins = settings.ALLOWED_ORIGINS.split(",") if settings.ALLOWED_ORIGINS else ["*"]
allowed_origins.append("http://localhost:8080")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.middleware("http")
async def log_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        import traceback
        logger.error(f"❌ Unhandled Exception: {str(e)}\n{traceback.format_exc()}")
        raise e

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from starlette.exceptions import HTTPException as StarletteHTTPException
    if isinstance(exc, StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    # This catches any unhandled exceptions and returns a clean 500 JSON response
    # instead of crashing the server or returning raw HTML errors.
    return JSONResponse(
        status_code=500,
        content={"detail": "Ocorreu um erro interno no servidor."},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    import traceback
    logger.error(f"❌ Validation Error for request {request.method} {request.url}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

# Incluir rotas
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(product_router)
app.include_router(flow_router)
app.include_router(feature_router)
app.include_router(history_router)
app.include_router(environment_router)
app.include_router(variable_router)
app.include_router(schedule_router, prefix="/schedules")
app.include_router(capture_router)
app.include_router(analysis_router)
app.include_router(agent_router)
app.include_router(execution_router)
app.include_router(import_router)
app.include_router(cicd_router)
app.include_router(skill_router)
app.include_router(appium_inspector_router, prefix="/mobile-inspector", tags=["Mobile Inspector"])
app.include_router(web_inspector_router, prefix="/web-inspector", tags=["Web Studio"])
app.include_router(hitl_router)
app.include_router(company_router)

# Dashboard Router
from app.routes.dashboard_routes import router as dashboard_router
app.include_router(dashboard_router)

# Proxy Router for bypassing CORS
from app.routes.proxy_routes import router as proxy_router
app.include_router(proxy_router)

# Front Recording Router
from app.routes.front_recording_routes import router as front_recording_router
app.include_router(front_recording_router)

# Performance Testing Router
from app.routes.performance_routes import router as performance_router
app.include_router(performance_router)

# Integration Router
from app.routes.integration_routes import router as integration_router
app.include_router(integration_router)

# Landing Routes
from app.routes.landing_routes import router as landing_router
app.include_router(landing_router, tags=["Landing Page"])

# Mount Videos Static Directory
from fastapi.staticfiles import StaticFiles
import os
# Video storage path (Cross-platform)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "..", "data", "videos")
os.makedirs(VIDEO_DIR, exist_ok=True)
app.mount("/videos", StaticFiles(directory=VIDEO_DIR), name="videos")

# Mount Screenshots directory
SCREENSHOT_DIR = os.path.join(os.path.dirname(VIDEO_DIR), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=SCREENSHOT_DIR), name="screenshots")

# Middleware para log de requests
SKIP_LOG_PATHS = {"/", "/status", "/favicon.ico"}

@app.middleware("http")
async def log_requests(request, call_next):
    # Skip log for health check and static paths to reduce noise
    if request.url.path in SKIP_LOG_PATHS or request.url.path.startswith(("/videos/", "/screenshots/")):
        return await call_next(request)

    logger.debug(f"REQUEST: {request.method} {request.url}")

    try:
        response = await call_next(request)
        if response.status_code == 404:
            logger.warning(f"🔍 [404 Diagnostic] Path: {request.url.path} | Method: {request.method} | Params: {request.query_params}")
            # Log all registered routes for comparison when 404 occurs
            # routes = [r.path for r in request.app.routes]
            # logger.debug(f"🔍 [404 Diagnostic] Available: {routes}")
        
        if response.status_code >= 400:
            logger.warning(f"RESPONSE: {request.method} {request.url} - Status: {response.status_code}")
        else:
            logger.debug(f"RESPONSE: {request.method} {request.url} - Status: {response.status_code}")
        return response
    except Exception as e:
        logger.error(f"ERROR in {request.method} {request.url}: {str(e)}")
        raise

# Rotas Utilitárias
@app.get("/", tags=["System"])
def read_root():
    return {
        "status": "online",
        "message": "API de Fluxo rodando corretamente",
        "version": "1.0.0",
    }

@app.get("/status", tags=["System"])
def api_status():
    routes = [{"path": getattr(route, "path", str(route)), "name": getattr(route, "name", "unknown")} for route in app.routes]
    return {"status": "online", "total_routes": len(routes)}

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host=host, port=port, reload=True, log_level="info")
