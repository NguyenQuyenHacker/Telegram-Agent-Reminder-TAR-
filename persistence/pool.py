import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text
from sqlmodel import Session, create_engine

from TAR_agent.utils.config import settings

# pool_size 10 chứ không phải 5 mặc định: một lượt nạp file chiếm vài kết nối
# trong lúc ghi chunk, chạy song song với luồng hỏi đáp là cạn pool và nghẽn cả
# hai. Trần số lượt nạp song song đặt ở app/routers/webhooks.py.
engine: Engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10)


def get_session() -> Session:
    return Session(engine)


@contextmanager
def readonly_tx(project_id: uuid.UUID) -> Iterator[Session]:
    """Session đã hạ quyền xuống `tar_ro` và chốt sẵn dự án. CHỈ dùng cho SQL
    do LLM sinh ra.

    KHÔNG có engine thứ hai, KHÔNG có DSN thứ hai: cùng connection, cùng pool,
    cùng `DATABASE_URL`. `tar_ro` là NOLOGIN nên nó không phải một cửa vào — nó
    chỉ là một cái mũ đội trong đúng một transaction.

    Đây là chỗ DUY NHẤT được mở session cho SQL của LLM. RLS chỉ áp cho role
    KHÔNG phải chủ bảng, mà app nối bằng role chủ — nên chạy SQL của model bằng
    một `get_session()` thường thì mọi policy trở thành trang trí, model thấy
    dòng của MỌI dự án và không có exception nào báo.

    Bốn dòng SET đều `LOCAL`: hết transaction là Postgres tự trả về, connection
    quay lại pool nguyên trạng. "Quên RESET" không phải một failure mode ở đây
    — không có `RESET` nào để quên.

    `search_path` phải set trong transaction chứ không phải bằng
    `ALTER ROLE ... SET`: cái đó chỉ áp lúc ĐĂNG NHẬP, mà tar_ro không bao giờ
    đăng nhập. Thiếu nó thì `FROM cong_viec` nổ "relation does not exist" —
    và `validate` đang CẤM model viết `data.` nên nó không tự chữa được.

    `SET TRANSACTION READ ONLY` là thừa (tar_ro vốn chỉ có SELECT) và cố ý giữ:
    một `GRANT` lỡ tay sau này không được phép trở thành đường ghi.

    Thứ tự không đổi được: `SET TRANSACTION READ ONLY` phải đứng trước câu lệnh
    thật đầu tiên của transaction, và `SET LOCAL ROLE` phải đứng trước nó để
    cái `statement_timeout` sau đó là của role đã hạ quyền.
    """
    with Session(engine) as session, session.begin():
        session.exec(text("SET LOCAL ROLE tar_ro"))  # type: ignore[call-overload]
        session.exec(text("SET TRANSACTION READ ONLY"))  # type: ignore[call-overload]
        session.exec(text("SET LOCAL search_path = data"))  # type: ignore[call-overload]
        session.exec(text("SET LOCAL statement_timeout = '5s'"))  # type: ignore[call-overload]
        # Tham số hoá chứ không nối chuỗi: `project_id` đã là uuid.UUID nên
        # không có đường tiêm, nhưng chỗ này là chỗ cuối cùng đáng để cẩn thận.
        session.exec(  # type: ignore[call-overload]
            text("SELECT set_config('app.project_id', :pid, true)"),
            params={"pid": str(project_id)},
        )
        yield session
