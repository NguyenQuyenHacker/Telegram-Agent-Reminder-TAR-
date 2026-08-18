import asyncio
import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.routers.webhooks import router as webhooks_router
from app.telegram.bots import close_bots, set_webhooks
from app.telegram.polling import start_dev_polling, stop_dev_polling
from app.telegram.download import sweep_stale_uploads
from TAR_agent.graph_admin.graph import build_admin_graph
from TAR_agent.graph_client.graph import build_client_graph
from TAR_agent.utils.config import settings
from persistence.pool import warm_up

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


def _checkpointer_dsn() -> str:
    """AsyncPostgresSaver dùng DSN psycopg thuần, không có tiền tố SQLAlchemy."""
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


# Số thread cho `asyncio.to_thread`. Đặt theo TRẦN ĐỒNG THỜI chứ không theo số
# nhân: việc trong các thread này là chờ mạng và chờ Postgres, không phải tính
# toán. _CLIENT_SLOTS(4) × 2 thread mỗi lượt hỏi (tra tài liệu + chạy SQL) +
# _INGEST_SLOTS(2) = 10, làm tròn lên cho thoáng.
_MAX_THREADS = 16


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Mặc định của event loop là min(32, cpu_count + 4) — trên máy deploy 1 vCPU
    # là 5 thread, 2 vCPU là 6. Thiếu thread thì hai nhánh của `retrieve` THÔI
    # KHÔNG còn song song: chúng xếp hàng chờ thread và `retrieve` âm thầm đi từ
    # max(nhánh A, nhánh B) về gần A + B.
    #
    # Đây là kiểu lỗi làm "22s ở local" thành "35s trên prod" mà không lần ra
    # được, vì nó KHÔNG tái hiện trên máy dev (12 nhân -> 16 thread, vừa đủ).
    executor = ThreadPoolExecutor(max_workers=_MAX_THREADS, thread_name_prefix="tar")
    asyncio.get_running_loop().set_default_executor(executor)

    # POOL chứ không phải một connection đơn (from_conn_string cũ): Neon
    # (serverless) ngắt kết nối idle sau một khoảng thời gian, một connection
    # đơn sống suốt vòng đời app thì CHẾT LUÔN không tự hồi phục
    # (psycopg.OperationalError: SSL connection has been closed unexpectedly —
    # gặp thật sau ~36 phút không có tin nhắn).
    #
    # `check` là thứ THỰC SỰ chữa được lỗi đó, không phải bản thân cái pool.
    # Mặc định pool KHÔNG kiểm tra connection trước khi giao: nó chỉ phát hiện
    # hỏng khi truy vấn đã nổ, tức là đã muộn — lượt chat đó mất trắng, chỉ
    # lượt SAU mới được connection mới. Log lộ đúng chuyện này:
    #   WARNING psycopg.pool | discarding closed connection: ... [BAD]
    #   ERROR   ... SSL connection has been closed unexpectedly
    # (vứt connection hỏng rồi vẫn để lời gọi đó chết theo).
    #
    # check_connection chạy một truy vấn rỗng trước khi giao; hỏng thì pool âm
    # thầm bỏ và mở connection mới. Trả giá một round-trip mỗi lần lấy
    # connection — với bot Telegram thì không đáng kể so với việc mất tin nhắn.
    #
    # kwargs của connection PHẢI khớp những gì AsyncPostgresSaver cần
    # (autocommit=True, prepare_threshold=0, row_factory=dict_row) — copy từ
    # chính source của AsyncPostgresSaver.from_conn_string.
    pool = AsyncConnectionPool(
        conninfo=_checkpointer_dsn(),
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        min_size=1,
        max_size=5,
        check=AsyncConnectionPool.check_connection,
        # Tự thay connection trước khi Neon kịp ngắt, thay vì chờ nó chết rồi
        # mới biết. Neon ngắt kết nối rảnh sau vài phút nên để dưới ngưỡng đó.
        max_idle=120.0,
        max_lifetime=900.0,
        open=False,
    )
    await pool.open(wait=True)
    try:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        # Hai graph dùng CHUNG một checkpointer — thread_id đã tách theo vai
        # (xem webhooks.thread_config) nên hai hội thoại không đụng nhau.
        app.state.admin_graph = build_admin_graph(checkpointer)
        app.state.client_graph = build_client_graph(checkpointer)

        # Pool của checkpointer đã nóng sẵn (`pool.open(wait=True)` ở trên chờ đủ
        # min_size). Engine SQLAlchemy thì lazy, phải tự đánh thức.
        await asyncio.to_thread(warm_up)
        await asyncio.to_thread(sweep_stale_uploads)

        # TẮT tạm: dọn checkpoint LangGraph lúc 3h sáng. Chưa xoá file
        # app/scheduler/runner.py — chỉ ngắt khỏi lifespan, bật lại thì gỡ
        # comment 2 dòng dưới và import build_scheduler ở đầu file.
        # scheduler = build_scheduler()
        # scheduler.start()

        # Có domain public -> webhook; dev local -> polling cho cả hai bot
        polling = None
        if settings.telegram_webhook_url:
            await set_webhooks()
        else:
            polling = await start_dev_polling(app)

        try:
            yield
        finally:
            # scheduler.shutdown(wait=False)  # bật lại cùng lúc với scheduler.start() ở trên
            if polling:
                await stop_dev_polling(polling)
            await close_bots()
    finally:
        # CancelledError: uvicorn --reload huỷ task của lifespan, mà pool.close()
        # lại chờ worker của chính nó thoát. Tiến trình đang chết tới nơi rồi,
        # connection sẽ đứt theo — nuốt để traceback này khỏi che mất lỗi thật.
        with suppress(asyncio.CancelledError):
            await pool.close()
        # wait=False: tiến trình đang tắt, không đứng chờ một lượt tra dở dang.
        executor.shutdown(wait=False)


app = FastAPI(title="TAR — kho tri thức tiến độ dự án", lifespan=lifespan)
app.include_router(webhooks_router)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}
