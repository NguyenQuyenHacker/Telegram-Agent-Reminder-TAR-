from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import settings

engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)


def get_session() -> Session:
    return Session(engine)
