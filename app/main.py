import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

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
from app.routes.capture_routes import router as capture_router
from app.routes.analysis_routes import router as analysis_router
from app.routes.agent_routes import router as agent_router
from app.routes.execution_routes import router as execution_router

# ===== CONFIGURAÇÃO DE LOGGING =====
setup_logging()
logger = logging.getLogger(__name__)

# ===== LIFESPAN MANAGER =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables and start scheduler
    logger.info("=== INICIANDO API QA-WORKFLOW ===")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Tabelas verificadas/criadas com sucesso")
    except Exception as e:
        logger.error(f"Erro ao criar tabelas: {e}")

    logger.info("Iniciando scheduler...")
    try:
        scheduler_service.start()
        db = SessionLocal()
        scheduler_service.sync_jobs(db)
        db.close()
    except Exception as e:
        logger.error(f"Erro ao iniciar scheduler: {e}")

    yield
    
    # Shutdown
    logger.info("Desligando aplicação...")
    if scheduler_service.scheduler.running:
        scheduler_service.scheduler.shutdown()

# ===== APP INIT =====
app = FastAPI(
    title="QA WorkFlow API",
    description="API para gerenciamento de fluxos de trabalho de QA",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Configuração Middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Incluir rotas
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(product_router)
app.include_router(flow_router)
app.include_router(feature_router)
app.include_router(history_router)
app.include_router(environment_router)
app.include_router(variable_router)
app.include_router(schedule_router, prefix="/schedules", tags=["Schedules"])
app.include_router(capture_router)
app.include_router(analysis_router)
app.include_router(agent_router)
app.include_router(execution_router)

# Middleware para log de requests
@app.middleware("http")
async def log_requests(request, call_next):
    # Skip log for health check to reduce noise
    if request.url.path == "/":
        return await call_next(request)

    logger.info(f"REQUEST: {request.method} {request.url}")
    try:
        response = await call_next(request)
        logger.info(f"RESPONSE: {request.method} {request.url} - Status: {response.status_code}")
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
    routes = [{"path": route.path, "name": route.name} for route in app.routes]
    return {"status": "online", "total_routes": len(routes)}

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host=host, port=port, reload=True, log_level="info")
