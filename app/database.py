"""SQLAlchemy engine and session factory."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_url = os.environ["DATABASE_URL"]
# Railway provides postgres:// URLs; SQLAlchemy 2 requires postgresql://
if _url.startswith("postgres://"):
    _url = "postgresql://" + _url[len("postgres://"):]

engine = create_engine(_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass
