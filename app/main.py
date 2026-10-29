from __future__ import annotations

import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import models  # noqa: F401 - registers SQLAlchemy metadata
from app.database import Base, engine
from app import ingestion
from app.routes import fleet_health, maintenance

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()

# Absolute path to the static directory
BASE_DIR = Path(__file__).resolve().parent
# If main.py is in app/, static is in ../static; if main.py is at root, it's ./static
STATIC_DIR = BASE_DIR / "static" if (BASE_DIR / "static").exists() else BASE_DIR.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if APP_ENV == "development":
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Fleet Health & Maintenance Agent (Reference MVP)",
    version="0.2.0",
    description="Predictive fleet health, DVIR NLP inspection triage, and automated dispatch hold engine.",
    docs_url="/docs" if APP_ENV in {"development", "test"} else None,
    redoc_url="/redoc" if APP_ENV in {"development", "test"} else None,
    lifespan=lifespan,
)
app = FastAPI
# -------------------------------------------------------------------
# CORS Configuration: Ensure local browser requests succeed
# -------------------------------------------------------------------
configured_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
# In development, default to permissive origins so file:// or localhost can query APIs
allowed_origins = configured_origins if configured_origins else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------
# API Routers
# -------------------------------------------------------------------
app.include_router(fleet_health.router)
app.include_router(maintenance.router)
app.include_router(ingestion.router)


# -------------------------------------------------------------------
# Serve Static Dashboard (index.html)
# -------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/dashboard", include_in_schema=False)
    async def serve_dashboard():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="Dashboard index.html not found")


# -------------------------------------------------------------------
# Health & Readiness Probes
# -------------------------------------------------------------------
@app.get("/health", tags=["Operations"])
def health_check():
    """Liveness probe: confirms API process is serving traffic."""
    return {"status": "ok", "service": "fleet-health-agent", "version": app.version}


@app.get("/ready", tags=["Operations"])
def readiness_check():
    """Readiness probe: confirms database connectivity."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database connection failed") from exc
    return {"status": "ready", "database": "ok"}