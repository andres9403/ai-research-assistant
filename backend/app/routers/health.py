from fastapi import APIRouter
from sqlalchemy import text

from app.db import engine

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
