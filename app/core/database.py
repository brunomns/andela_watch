"""SQLAlchemy engine / session / Base + DB initialization."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATA_DIR, settings

# Ensure the data directory exists before SQLite tries to open the file.
DATA_DIR.mkdir(parents=True, exist_ok=True)
(settings.SAMPLE_DIR).mkdir(parents=True, exist_ok=True)

connect_args = (
    {"check_same_thread": False} if settings.DB_URL.startswith("sqlite") else {}
)

engine = create_engine(
    settings.DB_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(seed_services: bool = True) -> None:
    """Create all tables (idempotent) and optionally register the service fleet."""
    from app.models import database_models  # noqa: F401  register mappers

    Base.metadata.create_all(bind=engine)

    if seed_services:
        from app.models.database_models import Service

        db = SessionLocal()
        try:
            existing = {s.name for s in db.query(Service).all()}
            for svc in settings.SERVICES:
                if svc["name"] not in existing:
                    db.add(Service(name=svc["name"], tier=svc["tier"],
                                   description=f"{svc['tier']} microservice"))
            db.commit()
        finally:
            db.close()
