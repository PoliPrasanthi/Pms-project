from __future__ import annotations

from typing import Generator, AsyncGenerator

from datetime import datetime, timezone
from sqlalchemy import Column, Boolean, DateTime, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import func
from urllib.parse import quote_plus
from logging import getLogger

from app.core.config import settings

logger = getLogger("app.database")

# ---------------------------------------------------------------------------
# Sync engine — used only by: background workers, security.py dep checks
# ---------------------------------------------------------------------------
connect_args: dict = {}
if "azure" in settings.DB_SERVER:
    connect_args = {"ssl": {"check_hostname": False}}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    echo=settings.DB_ECHO,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

sync_engine = engine

# ---------------------------------------------------------------------------
# Async engine — used by all API endpoints
# ---------------------------------------------------------------------------
_async_connect_args: dict = {}
if "azure" in settings.DB_SERVER:
    import ssl
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _async_connect_args = {"ssl": ctx}

async_engine = create_async_engine(
    settings.ASYNC_DATABASE_URL,
    connect_args=_async_connect_args,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    echo=settings.DB_ECHO,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    autocommit=False,
    autoflush=False,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ---------------------------------------------------------------------------
# ORM Base & Mixins
# ---------------------------------------------------------------------------
Base = declarative_base()


class AuditMixin:
    created_at = Column(
        DateTime(timezone=False),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.utc_timestamp(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=False),
        default=None,
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=True,
    )
    is_active  = Column(Boolean, default=True,  nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ensure_database_exists():
    try:
        encoded_password = quote_plus(settings.DB_PASSWORD)
        server_url = f"mysql+pymysql://{settings.DB_USER}:{encoded_password}@{settings.DB_SERVER}:{settings.DB_PORT}/"

        temp_engine = create_engine(server_url, connect_args=connect_args)
        with temp_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {settings.DB_NAME}"))
        temp_engine.dispose()
        logger.info(f"Ensured database '{settings.DB_NAME}' exists.")
    except Exception as e:
        logger.error(f"Failed to ensure database exists: {e}")
        pass


def get_sync_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as db:
        try:
            yield db
        except Exception:
            await db.rollback()
            raise


# Alias kept for backward-compat with any code that imported get_db
get_db = get_sync_db
