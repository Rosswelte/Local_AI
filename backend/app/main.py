import base64
import binascii
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import _save_profile, router
from app.config import Settings
from app.database.database import Database
from app.hardware.detector import detect_profile
from app.orchestrator.jobs import JobManager, restart_jobs
from app.orchestrator.resource_manager import ResourceManager
from app.orchestrator.scheduler import Scheduler
from app.providers.ollama import OllamaProvider
from app.providers.manager import ProviderManager
from app.services.catalog import load_catalog, upsert_catalog
from app.services.manager import ServiceManager
from app.services.settings import read_settings
from app.errors import AppError


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / "database" / "orchestrator.db")
    await db.start(Path(__file__).parent / "database" / "migrations")
    app.state.db = db
    await db.write(upsert_catalog, load_catalog(Path(__file__).parents[1] / "catalog" / "models.yaml"))
    profile = detect_profile()
    app.state.profile = profile
    await db.write(_save_profile, profile)
    await db.write(restart_jobs)
    ollama = OllamaProvider(settings.ollama_url)
    app.state.ollama = ollama
    app.state.providers = {"ollama": ollama}
    app.state.provider_manager = ProviderManager(app.state.providers)
    app.state.service_manager = ServiceManager(db, app.state.providers)
    await app.state.service_manager.ensure_local_services(settings.ollama_url)
    try:
        installed = await ollama.installed_models()
        await db.write(_sync_installed_names, {item.get("name") for item in installed})
    except Exception:
        logging.info("Ollama is offline during startup", exc_info=True)
    configured = await db.read(read_settings)
    ram_budget = configured["ram_budget_mb"]["value"] if configured["ram_budget_mb"]["value"] is not None else profile.ram_budget_mb
    vram_budget = configured["vram_budget_mb"]["value"] if configured["vram_budget_mb"]["value"] is not None else profile.vram_budget_mb
    resources = ResourceManager({"ram_mb": int(ram_budget), "vram_mb": int(vram_budget)}, app.state.providers)
    app.state.resources = resources
    try:
        await resources.sync_loaded("ollama", await ollama.loaded_models())
    except Exception:
        logging.info("Unable to synchronize loaded Ollama models during startup", exc_info=True)
    allow_parallel = configured["allow_parallel"]["value"]
    worker_count = 2 if allow_parallel is True else 1
    app.state.scheduler = Scheduler(worker_count)
    app.state.jobs = JobManager(db, app.state.providers, resources, app.state.scheduler)
    await app.state.jobs.start()
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        await app.state.jobs.stop()
        await ollama.close()
        await db.close()


app = FastAPI(title="Orchestrateur IA", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def external_auth(request: Request, call_next):
    settings = getattr(request.app.state, "settings", None)
    protected = settings and settings.expose_host != "127.0.0.1" and request.url.path.startswith("/api/v1")
    public = request.url.path in {"/api/v1/health", "/api/v1/ready"}
    if protected and not public:
        password = request.headers.get("x-admin-password")
        authorization = request.headers.get("authorization", "")
        if not password and authorization.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(authorization[6:]).decode("utf-8")
                _, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError, binascii.Error):
                password = None
        if not password or not secrets.compare_digest(password, settings.admin_password):
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Basic realm=orchestrator"},
                content={"error": {"code": "unauthorized", "message": "Authentification requise", "details": {}}},
            )
    return await call_next(request)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.http_status, content=exc.body())


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": "http_error", "message": str(exc.detail), "details": {}}})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Requête invalide", "details": {"errors": exc.errors()}}})


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception):
    logging.exception("Unhandled application error")
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Erreur interne", "details": {}}})


def _sync_installed_names(con, names):
    con.execute("UPDATE models SET installed=0")
    for name in names:
        con.execute("UPDATE models SET installed=1 WHERE name=?", (name,))


frontend = Path(__file__).parents[2] / "frontend"
if frontend.exists():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
