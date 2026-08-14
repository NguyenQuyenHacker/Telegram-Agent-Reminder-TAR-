from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from TAR_agent.utils.config import settings

# pool_size 10 chứ không phải 5 mặc định: một lượt nạp file chiếm vài kết nối
# trong lúc ghi chunk, chạy song song với luồng hỏi đáp là cạn pool và nghẽn cả
# hai. Trần số lượt nạp song song đặt ở app/routers/webhooks.py.
engine: Engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10)


def get_session() -> Session:
    return Session(engine)
