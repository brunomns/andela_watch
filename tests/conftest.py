"""Shared pytest fixtures. Forces a dedicated SQLite test DB before app import."""
import os

os.environ["WATCHDOG_DB_URL"] = "sqlite:///./test_observability.db"
os.environ.setdefault("WATCHDOG_ALERT_COOLDOWN_SEC", "600")

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, SessionLocal, engine, init_db
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    init_db(seed_services=True)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)
