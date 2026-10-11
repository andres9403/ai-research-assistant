from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import REPO_ROOT, settings
from app.db import SessionLocal, init_db
from app.routers import ai, health, papers, search, upload
from app.routers import settings as settings_router
from app.services.pipeline import recover_interrupted, refresh_token_estimates

# Written by `npm run build` (and start.sh / start.ps1).
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        recover_interrupted(session)
        refresh_token_estimates(session)
    yield


def create_app(frontend_dist: Path = FRONTEND_DIST) -> FastAPI:
    """Build the app: every API router, then the built frontend at / if there is one.

    Serving the build lets one server run the whole app at a single URL. It is
    mounted last, so /api and /docs still reach FastAPI. In development,
    `npm run dev` serves the frontend instead and proxies /api here.
    """
    app = FastAPI(title="AI Research Assistant", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(search.router)
    app.include_router(upload.router)
    app.include_router(papers.router)
    app.include_router(ai.router)
    app.include_router(settings_router.router)
    if (frontend_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
