from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(db_url: str) -> None:
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        raw_path = db_url.replace("sqlite:///", "", 1)
        db_file = Path(raw_path)
        if db_file.parent:
            db_file.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(db_url: str | None = None) -> Engine:
    settings = get_settings()
    target_url = db_url or settings.jobrecall_db_url
    _ensure_sqlite_dir(target_url)

    connect_args = {"check_same_thread": False} if target_url.startswith("sqlite") else {}
    engine = create_engine(target_url, connect_args=connect_args, future=True)

    if target_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON;")
            if ":memory:" not in target_url:
                cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.close()

    return engine


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(target_engine: Engine | None = None) -> None:
    from app.models import entities  # noqa: F401
    Base.metadata.create_all(bind=target_engine or engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
