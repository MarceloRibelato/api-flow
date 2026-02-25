import logging
import os
from contextlib import asynccontextmanager

import httpx # Added for global client
from fastapi import FastAPI, Request
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
from app.routes.analysis_routes import router as analysis_router
from app.routes.agent_routes import router as agent_router
from app.routes.execution_routes import router as execution_router
from app.routes.import_routes import router as import_router
from app.routes.cicd_routes import router as cicd_router

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
            Base.metadata.create_all(bind=engine)
            logger.info("Tabelas verificadas/criadas com sucesso")
            break
        except Exception as e:
            if i < max_retries - 1:
                logger.warning(f"DB ainda não disponível, aguardando {retry_interval}s... Erro: {e}")
                time.sleep(retry_interval)
            else:
                logger.error(f"Erro CRÍTICO ao conectar no DB após {max_retries} tentativas: {e}")
                # We could raise here to crash the container and let Docker restart it, 
                # but for now let's just log critical error.
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
    app.state.http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True, verify=False)
    logger.info("Global HTTP Client initialized")

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
app.include_router(schedule_router, prefix="/schedules")
app.include_router(capture_router)
app.include_router(analysis_router)
app.include_router(agent_router)
app.include_router(execution_router)
app.include_router(import_router)
app.include_router(cicd_router)

# Dashboard Router
from app.routes.dashboard_routes import router as dashboard_router
app.include_router(dashboard_router)

# Proxy Router for bypassing CORS
from app.routes.proxy_routes import router as proxy_router
app.include_router(proxy_router)

# Mount Videos Static Directory
from fastapi.staticfiles import StaticFiles
import os
os.makedirs("/app/data/videos", exist_ok=True)
app.mount("/videos", StaticFiles(directory="/app/data/videos"), name="videos")

# Middleware para log de requests
@app.middleware("http")
async def log_requests(request, call_next):
    # Skip log for health check to reduce noise
    if request.url.path == "/":
        return await call_next(request)

    logger.info(f"REQUEST: {request.method} {request.url}")
    # try:
    #     body = await request.body()
    #     if body:
    #         logger.info(f"REQUEST BODY: {body.decode('utf-8', errors='ignore')[:1000]}")
    # except:
    #     pass

    try:
        response = await call_next(request)
        logger.info(f"RESPONSE: {request.method} {request.url} - Status: {response.status_code}")
        
        # Log response body for 422
        if response.status_code == 422:
            try:
                # We can't read the response body here easily without consuming it, 
                # but we can log the request url which is already done.
                pass
            except:
                pass
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
