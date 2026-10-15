import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import create_app

TEST_TOKEN = "test-jobrecall-secret-token"


def get_test_settings() -> Settings:
    return Settings(
        jobrecall_host="127.0.0.1",
        jobrecall_port=8765,
        jobrecall_api_token=TEST_TOKEN,
        jobrecall_db_url="sqlite:///:memory:",
        llm_provider="mock",
        llm_model="mock-v1",
        llm_timeout_seconds=5.0,
    )


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = get_test_settings
    with TestClient(app, headers={"X-JobRecall-Token": TEST_TOKEN}) as c:
        yield c


@pytest.fixture()
def sample_postings() -> dict[str, dict]:
    fixture_path = Path(__file__).parent / "fixtures" / "sample_postings.json"
    items = json.loads(fixture_path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in items}
