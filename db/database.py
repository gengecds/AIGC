"""数据库连接管理"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session as SASession
from typing import Optional

from db.models import Base

_engine = None
_SessionLocal = None


def get_engine(db_url: str = "sqlite:///storage/ai_drama.db"):
    global _engine
    if _engine is None:
        _engine = create_engine(db_url, echo=False, pool_pre_ping=True)
        Base.metadata.create_all(_engine)
    return _engine


def get_session(db_url: str = "sqlite:///storage/ai_drama.db") -> SASession:
    global _SessionLocal, _engine
    if _SessionLocal is None:
        engine = get_engine(db_url)
        _SessionLocal = sessionmaker(bind=engine)
    return _SessionLocal()


def close_engine():
    global _engine, _SessionLocal
    if _engine:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
