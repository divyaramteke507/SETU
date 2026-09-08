"""
SETU Database — SQLite setup and session management.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db():
    """Create all tables. Safe to call multiple times."""
    from models import (  # noqa: F401 — imported for side-effect of table registration
        Report,
        Extraction,
        Incident,
        IncidentReport,
        RelatedIncident,
        Contradiction,
        AuditLog,
    )
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
