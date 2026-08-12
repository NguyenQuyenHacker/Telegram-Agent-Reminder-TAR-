import asyncio
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings
from app.routers.webhooks import router as webhooks_router
from app.scheduler.runner import build_scheduler
from app.telegram.bot import bot, set_webhook
from app.telegram.polling import start_dev_polling, stop_dev_polling
from reminder_agent.config.settings import load_config
from reminder_agent.graph import ReminderAgent
from reminder_agent.prompts.loader import warm_up as warm_up_prompts

# Đổ .env vào os.environ. pydantic-settings đã tự đọc .env cho Settings, nhưng
# chỉ để điền field của nó chứ KHÔNG ghi vào os.environ — nên thư viện nào đọc
# thẳng os.getenv (Langfuse với LANGFUSE_*) sẽ không thấy gì nếu thiếu dòng này.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tar")


def _checkpointer_dsn() -> str:
    """AsyncPostgresSaver dùng DSN psycopg thuần, không có tiền tố SQLAlchemy."""
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncPostgresSaver.from_conn_string(_checkpointer_dsn()) as checkpointer:
        await checkpointer.setup()
        # Dựng trong lifespan chứ không ở module level như template LangGraph
        # Studio: checkpointer là async context manager, chỉ có sau khi app khởi
        # động và phải đóng lại khi tắt.
        agent = ReminderAgent(checkpointer)
        app.state.agent = agent
        app.state.graph = agent.build_graph()
        # Trả trước tiền chờ mạng của Langfuse, đừng để nó rơi vào tin nhắn đầu
        await asyncio.to_thread(warm_up_prompts)
        models = load_config()["models"]
        log.info(
            "Graph Job C sẵn sàng: %d tool, extract=%s, agent=%s",
            len(agent.tools),
            models["extract"]["model"],
            models["agent"]["model"],
        )

        scheduler = build_scheduler()
        scheduler.start()
        log.info("Job A chạy nền mỗi %d phút", settings.job_a_interval_minutes)

        # Có domain public -> webhook; dev local -> polling để vẫn nhận được tin nhắn
        polling = None
        if settings.telegram_webhook_url:
            await set_webhook()
        else:
            polling = await start_dev_polling(app)

        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            if polling:
                await stop_dev_polling(*polling)
            await bot.session.close()


app = FastAPI(title="TAR — Telegram Agent Reminder", lifespan=lifespan)
app.include_router(webhooks_router)


@app.get("/health")
async def health():
    return {"ok": True}
