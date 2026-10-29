"""Database setup for the standalone Fleet Health reference service.

The local .env file is loaded for development. Environment variables supplied by
Docker/systemd/the deployment platform take precedence. Production integration
must use the TMS database and migration system rather than create_all().
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv(override=False)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fleet_health_dev.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
