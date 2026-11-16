"""Database engine + session handling (SQLAlchemy 2.0).
Production/dev: PostgreSQL (psycopg 3 driver). SQLite is still accepted for quick tests."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def normalize_url(url: str) -> str:
    """Hosted providers hand out 'postgres://' or 'postgresql://' URLs; make them use psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = normalize_url(settings.database_url)
IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # pool_pre_ping drops dead connections (e.g. after the DB restarts) instead of failing a request
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: one DB session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
