import json
from collections.abc import Iterator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    return create_engine(
        url,
        connect_args={"check_same_thread": False},
        # Store JSON (e.g. author lists) unescaped so text filters match "José".
        json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
    )


engine = make_engine(settings.db_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (registers tables)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    add_missing_columns()


def add_missing_columns() -> None:
    """Add columns that later milestones introduced to tables an older version created.

    New columns are always nullable, so a plain ADD COLUMN is enough; there is no
    migration tool in this project.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    ddl = column.type.compile(engine.dialect)
                    conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}')


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
