"""Nối dây vòng sinh SQL tự sửa.

    START → gen_sql → validate ─ đạt ─→ execute ─ ok ─→ END
               │         │                  │
               │         └──→ repair ←──────┘  (lỗi SQL, đưa NGUYÊN VĂN thông
               │                │               báo của Postgres cho model)
               │                └──→ validate
               │                 │
               └─────────────────┴─ "KHONG_TRA_LOI_DUOC: ..." ──→ END

Nhánh cuối là đường TỪ CHỐI: model được phép nói "bảng không có trường này" thay
vì buộc phải nặn ra một câu SQL. Nó ra thẳng END chứ không qua `validate` — xem
`_after_write`. `tools/retrieval.py` dịch nó thành `table: "unsupported"`.

KHÔNG có `list_tables` / `get_schema`: vòng discovery của pattern SQL agent sinh
ra cho trường hợp không biết trước schema. Của ta là MỘT bảng, biết từ lúc viết
code, nên schema dán thẳng vào prompt. Vòng đáng giữ là `repair`, ánh xạ một-một
với cặp `grade → rewrite` của subgraph tra cứu.

Compile một lần lúc import, KHÔNG checkpointer: đây là một phép tính bên trong
một lời gọi tra cứu, không phải một hội thoại có điểm dừng.
"""

import asyncio
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text

from TAR_agent.graph_client.subgraph_sql.nodes import (
    to_jsonable,
    unsupported_reason,
    validate,
)
from TAR_agent.graph_client.subgraph_sql.state import SqlState
from TAR_agent.utils.config import create_google_genai, load_config, load_prompt, now_local
from TAR_agent.utils.timing import timed_node
from persistence.pool import readonly_tx

log = logging.getLogger(__name__)

# Model hay gói SQL trong ```sql ... ``` dù prompt đã dặn đừng. Gỡ ở đây rẻ hơn
# một vòng `repair` chỉ để nó bỏ ba dấu backtick.
_FENCE = re.compile(r"^\s*```(?:sql)?\s*|\s*```\s*$", re.IGNORECASE)


class SqlGraph:
    """Vòng sinh SQL tự sửa. Compile một lần, không checkpointer."""

    def __init__(self) -> None:
        config = load_config()
        retrieval = config["retrieval"]
        self.max_attempts = retrieval["max_sql_attempts"]
        self.row_limit = retrieval["sql_row_limit"]
        # MỘT model cho cả `gen_sql` lẫn `repair`: viết SQL và sửa SQL là cùng
        # một việc với cùng một schema, khác nhau ở chỗ lần sau có thêm thông
        # báo lỗi trong đầu vào.
        self.sql_writer = create_google_genai(config["models"]["sql"])

    async def _ask(self, question: str, extra: str = "") -> str:
        system = load_prompt(
            "client_system/gen_sql",
            TODAY=now_local().strftime("%d/%m/%Y"),
            QUESTION=question,
        )
        reply = await self.sql_writer.ainvoke(
            {
                "system": [SystemMessage(system)],
                "messages": [HumanMessage(extra or question)],
            }
        )
        return _FENCE.sub("", str(reply.content)).strip()

    async def _gen_sql(self, state: SqlState) -> dict:
        """Hỏng (lỗi mạng) thì trả SQL rỗng, KHÔNG ném.

        Cùng nguyên tắc "hỏng thì mở chứ không đóng" của `grade`: `validate` sẽ
        từ chối chuỗi rỗng, vòng đi tiếp bình thường và cuối cùng trả `rows`
        rỗng — thay vì một exception nổ giữa lời gọi tool và giết cả lượt hỏi.
        """
        try:
            sql = await self._ask(state["question"])
        except Exception:
            log.exception("Sinh SQL thất bại")
            sql = ""
        if reason := unsupported_reason(sql):
            log.info("Từ chối sinh SQL: %s", reason)
            return {"sql": "", "attempt": 1, "unsupported": reason}
        log.info("SQL lượt 1: %s", sql or "(rỗng)")
        return {"sql": sql, "attempt": 1, "unsupported": None}

    async def _repair(self, state: SqlState) -> dict:
        """Sửa câu vừa trượt. TĂNG `attempt` Ở ĐÂY, không ở router.

        Router của LangGraph chỉ chọn đường, nó không ghi được state — cùng bài
        học với `revise_count` ở `compose`.
        """
        payload = (
            f"CÂU HỎI:\n{state['question']}\n\n"
            f"CÂU SQL VỪA THỬ:\n{state.get('sql', '')}\n\n"
            f"LỖI:\n{state.get('error') or '(không rõ)'}\n\n"
            f"Viết lại câu SQL cho đúng. Chỉ trả câu lệnh."
        )
        try:
            sql = await self._ask(state["question"], payload)
        except Exception:
            log.exception("Sửa SQL thất bại")
            sql = ""
        attempt = state.get("attempt", 1) + 1
        # Từ chối được cả ở lượt sửa: câu đầu có thể trượt `validate` vì lý do
        # cú pháp, rồi model mới nhận ra bảng không có trường cần thiết.
        if reason := unsupported_reason(sql):
            log.info("Từ chối sửa SQL: %s", reason)
            return {"sql": "", "attempt": attempt, "unsupported": reason}
        log.info("SQL lượt %d: %s", attempt, sql or "(rỗng)")
        return {"sql": sql, "attempt": attempt, "unsupported": None}

    def _validate(self, state: SqlState) -> dict:
        reason, cleaned = validate(state.get("sql", ""), self.row_limit)
        if reason:
            log.info("Câu SQL bị từ chối: %s", reason)
        return {"sql": cleaned, "error": reason}

    async def _execute(self, state: SqlState) -> dict:
        """Chạy trong `readonly_tx`, KHÔNG bao giờ mở session thường.

        Toàn bộ phần hạ quyền (`SET LOCAL ROLE tar_ro` + `app.project_id`) nằm
        trong context manager đó, nên node này không có cách nào viết đúng một
        nửa. Mở `Session(engine)` ở đây là role CHỦ chạy SQL của model — chủ
        bảng bypass RLS mà không báo gì, và bot trả lời câu của dự án này bằng
        dữ liệu dự án khác.
        """
        sql = state["sql"]

        def run() -> tuple[list[str], list[dict]]:
            with readonly_tx(state["project_id"]) as session:
                result = session.exec(text(sql))  # type: ignore[call-overload]
                columns = list(result.keys())
                return columns, [
                    {key: to_jsonable(value) for key, value in zip(columns, row)}
                    for row in result.fetchall()
                ]

        try:
            columns, rows = await asyncio.to_thread(run)
        except Exception as exc:
            # NGUYÊN VĂN thông báo của Postgres đi thẳng vào prompt của
            # `repair`: "column ngay_hoan_thanh does not exist" là thứ model
            # sửa được, còn một mã lỗi tự đặt thì không.
            message = str(getattr(exc, "orig", exc)).strip()
            log.warning("Chạy SQL hỏng: %s", message)
            return {"ok": False, "error": message, "rows": [], "columns": []}

        log.info("SQL trả %d dòng", len(rows))
        return {"ok": True, "error": None, "rows": rows, "columns": columns}

    def _after_write(self, state: SqlState) -> str:
        """Dùng chung cho `gen_sql` và `repair`.

        Lời từ chối phải ra thẳng END, KHÔNG đi qua `validate`: `validate` sẽ
        thấy một chuỗi không mở đầu bằng SELECT, gọi đó là rác, và đá sang
        `repair` — rồi `repair` nặn ra một câu SQL cho bằng được. Đúng cái hành
        vi bịa số mà cả thay đổi này sinh ra để chặn.
        """
        return "end" if state.get("unsupported") else "validate"

    def _after_validate(self, state: SqlState) -> str:
        if not state.get("error"):
            return "execute"
        if state.get("attempt", 0) >= self.max_attempts:
            return "end"
        return "repair"

    def _after_execute(self, state: SqlState) -> str:
        if state.get("ok"):
            return "end"
        # Hết lượt -> ra bằng cửa END với `rows` rỗng, không trả kết quả nửa
        # vời. Cùng lý do `grade` trả rỗng ở lượt cuối: một lô dữ liệu sai đưa
        # cho `compose` là mời nó dựng một câu trả lời nghe hợp lý mà không
        # dòng nào chứng minh được.
        if state.get("attempt", 0) >= self.max_attempts:
            return "end"
        return "repair"

    def build(self):
        builder = StateGraph(SqlState)
        # `validate` không bọc đồng hồ: Python thuần, không LLM, không DB — đo nó
        # chỉ thêm một dòng log 0ms vào mọi lượt.
        builder.add_node("gen_sql", timed_node("sql.gen", self._gen_sql))
        builder.add_node("validate", self._validate)
        builder.add_node("execute", timed_node("sql.execute", self._execute))
        builder.add_node("repair", timed_node("sql.repair", self._repair))

        builder.add_edge(START, "gen_sql")
        builder.add_conditional_edges(
            "gen_sql", self._after_write, {"validate": "validate", "end": END}
        )
        builder.add_conditional_edges(
            "validate",
            self._after_validate,
            {"execute": "execute", "repair": "repair", "end": END},
        )
        builder.add_conditional_edges(
            "execute", self._after_execute, {"repair": "repair", "end": END}
        )
        builder.add_conditional_edges(
            "repair", self._after_write, {"validate": "validate", "end": END}
        )
        # `checkpointer=False` chứ không phải bỏ trống — xem chú thích cùng chỗ
        # trong subgraph/graph.py. Hai nhánh của `retrieve_all` chạy song song TRONG
        # một node, nên cả hai đều phải tự chối checkpointer của graph cha.
        return builder.compile(checkpointer=False)


# Dựng một lần lúc import. `tools/retrieval.py` gọi lại nó ở mọi lượt tra.
SQL_GRAPH = SqlGraph().build()
