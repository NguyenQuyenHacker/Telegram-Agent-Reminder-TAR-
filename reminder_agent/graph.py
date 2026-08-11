"""Job C: agent xử lý báo cáo và hỏi đáp.

Toàn bộ node là method của ReminderAgent, để mọi thứ một lượt chạy cần —
vai LLM, tool, checkpointer — nằm trong self, khai báo một chỗ ở __init__ thay
vì rải khắp các hàm module-level.

Graph có hai nhánh:
  báo cáo:  extract_tasks -> ask_confirm -> read_decision -(duyệt)-> save_to_db
                 ^                ^ interrupt      |
                 +---(sửa)--------+---(chưa rõ)----+
  hỏi đáp:  call_model <-> call_tools -> answer
"""

import asyncio
import hashlib
import logging
import os
import re
from datetime import date
from typing import Literal

from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.datetime_utils import now_local, weekday_vi
from app.core.priority import clamp_to_window, interval_for
from app.core.task_text import normalize_content
from app.telegram.messages import (
    abandoned_text,
    extraction_summary_text,
    missing_due_date_prompt,
    need_reason_text,
    no_answer_text,
    saved_text,
)
from app.telegram.sender import send_message
from persistence.models.task import Priority, TaskStatus
from persistence.proc.reports import insert_report
from persistence.proc.tasks import upsert_task
from reminder_agent.config.settings import create_google_genai, load_config
from reminder_agent.prompts.loader import load_prompt
from reminder_agent.utils.intent import parse_free_text_decision
from reminder_agent.utils.schemas import ExtractionResult
from reminder_agent.utils.state import GraphState
from reminder_agent.utils.tools import ALL_TOOLS

log = logging.getLogger(__name__)


class ReminderAgent:
    """Agent nhắc việc: trích đầu việc từ báo cáo, và trả lời câu hỏi.

    Khác template ReAct thuần: nhánh báo cáo có chốt chặn người duyệt — dừng ở
    ask_confirm bằng interrupt(), người dùng phản hồi thì route_after_confirm
    rẽ sang save_to_db / trích lại / bỏ.

    Tự dựng StateGraph vì create_agent của langgraph 0.2 không mô hình hoá được
    luồng dừng-chờ-người này. (LangChain 1.x có HumanInTheLoopMiddleware làm
    việc tương tự, nhưng nó chặn ở lời gọi tool, còn chốt chặn ở đây nằm ở kết
    quả trích xuất — muốn dùng phải biến save_to_db thành tool và nâng cả stack.)
    """

    def __init__(self, checkpointer: BaseCheckpointSaver) -> None:
        self.checkpointer = checkpointer
        config = load_config()

        self.tools = ALL_TOOLS
        self._tools_by_name = {t.name: t for t in self.tools}
        self.max_tool_rounds = config["agent"]["max_tool_rounds"]
        self.max_history_messages = config["agent"]["max_history_messages"]

        # System prompt không nằm trong LLM mà truyền lúc gọi, nên dựng sẵn được
        models = config["models"]
        self.task_extractor = create_google_genai(
            models["extract"], output_schema=ExtractionResult
        )
        self.qa_assistant = create_google_genai(models["agent"], tools=self.tools)

    # ------------------------------------------------------------------
    # Nhánh báo cáo
    # ------------------------------------------------------------------

    @staticmethod
    def compute_task_id(group: str, content: str) -> str:
        """R7: cùng nhóm + cùng nội dung luôn ra cùng mã, để đồng bộ lại không trùng.

        Băm bản đã gọt "(hạn ...)" chứ không băm nguyên văn: hạn là thuộc tính
        của công việc, không phải căn cước của nó. Băm cả hạn thì báo cáo sửa
        ngày sẽ đẻ ra dòng mới, còn dòng cũ nằm lại nhắc theo hạn đã lỗi thời —
        đúng thứ R7 sinh ra để chống.
        """
        collapsed_content = re.sub(r"\s+", " ", normalize_content(content).lower())
        identity_key = f"{group.strip().lower()}|{collapsed_content}"
        return hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:16]

    async def extract_tasks(self, state: GraphState) -> dict:
        """R1/R2/R9: trích đầu việc ở khối 'Tiếp theo:', gán mức ưu tiên, không đoán hạn."""
        report_text = state.get("raw_report_text") or ""
        edit_request = state.get("edit_request")

        system_prompt = load_prompt("extract_system", TODAY=now_local().date().isoformat())
        if edit_request:
            system_prompt += "\n\n" + load_prompt(
                "extract_retry", EDIT_REQUEST=edit_request
            )

        extraction = await self.task_extractor.ainvoke(
            {
                "system": [SystemMessage(content=system_prompt)],
                "messages": [HumanMessage(content=report_text)],
            }
        )

        tasks = [task.model_dump() for task in extraction.tasks]
        log.info("EXTRACT: trích được %d đầu việc", len(tasks))

        # Chỉ lưu báo cáo ở lần trích đầu. Nhánh sửa quay lại chính node này,
        # lưu tiếp là nhân bản cùng một báo cáo trong DB.
        if not edit_request:
            await asyncio.to_thread(insert_report, report_text)

        # Gửi bảng ở đây chứ không ở ask_confirm: node có interrupt() sẽ chạy
        # lại từ đầu mỗi lần resume, để send_message trong đó thì người dùng
        # nhận lại bảng cũ mỗi lần họ trả lời.
        await send_message(state["chat_id"], extraction_summary_text(tasks))
        return {"extracted_tasks": tasks, "edit_request": None}

    def ask_confirm(self, state: GraphState) -> dict:
        """Chốt chặn người duyệt: graph dừng tại đây tới khi người dùng phản hồi.

        Node này CỐ Ý không làm gì ngoài interrupt(). Node chứa interrupt() chạy
        lại từ đầu mỗi lần resume, nên mọi side effect đặt ở đây — gửi tin nhắn,
        gọi LLM — sẽ lặp lại một lần cho mỗi lượt trả lời. Việc đọc câu trả lời
        tách sang read_decision, node thường, chạy đúng một lần.

        Giá trị trả về của interrupt() là nguyên văn tin nhắn người dùng, do
        webhook đưa vào qua Command(resume=text).
        """
        reply = interrupt(
            {"awaiting": "confirm", "tasks": state.get("extracted_tasks", [])}
        )
        return {"confirm_reply": reply}

    async def read_decision(self, state: GraphState) -> dict:
        """Đọc ý người duyệt: duyệt / sửa / bỏ / chưa rõ.

        Trước đây chạy ở webhook nên nằm ngoài vùng phủ của Langfuse — đây là
        lời gọi LLM thứ ba của hệ thống mà trace không thấy. Đưa vào node thì
        callback gắn ở tầng graph phủ được luôn.
        """
        decision = await parse_free_text_decision(state.get("confirm_reply") or "")
        status = decision["status"]
        edit_request = decision["edit_request"]

        # Muốn sửa mà chưa nói sửa chỗ nào thì coi như chưa rõ: trích lại với
        # tay trắng chỉ ra đúng kết quả cũ.
        if status == "edit" and not edit_request:
            status = "unclear"

        log.info("CONFIRM status=%s chat_id=%s", status, state["chat_id"])

        if status == "unclear":
            await send_message(state["chat_id"], need_reason_text())
        elif status == "abandoned":
            await send_message(state["chat_id"], abandoned_text())

        return {"confirm_status": status, "edit_request": edit_request}

    def route_after_confirm(self, state: GraphState) -> str:
        status = state.get("confirm_status")
        if status == "approved":
            return "save_to_db"
        if status == "edit":
            # read_decision đã hạ "edit" thiếu edit_request xuống "unclear", nên
            # tới đây chắc chắn có chỗ cần sửa để trích lại
            return "extract_tasks"
        if status == "unclear":
            # Quay lại hỏi tiếp, KHÔNG kết thúc: bảng đầu việc vẫn đang chờ duyệt
            return "ask_confirm"
        return END

    async def save_to_db(self, state: GraphState) -> dict:
        """R7: upsert theo task_id; R9: hỏi bổ sung hạn đúng một lần."""
        tasks = state.get("extracted_tasks", [])
        if not tasks:
            await send_message(state["chat_id"], abandoned_text())
            return {}

        first_remind_at = clamp_to_window(now_local())
        for task in tasks:
            priority = Priority(task["priority"])
            saved_task = await asyncio.to_thread(
                upsert_task,
                self.compute_task_id(task["group"], task["content"]),
                task["group"],
                task["content"],
                date.fromisoformat(task["due_date"]) if task.get("due_date") else None,
                priority,
                interval_for(priority, TaskStatus.pending),
                first_remind_at,
            )
            if saved_task.due_date is None:
                await send_message(state["chat_id"], missing_due_date_prompt(saved_task))

        log.info("SAVE: đã lưu %d đầu việc", len(tasks))
        await send_message(state["chat_id"], saved_text(len(tasks)))
        return {}

    # ------------------------------------------------------------------
    # Nhánh hỏi đáp
    # ------------------------------------------------------------------

    def _recent_history(self, state: GraphState) -> list:
        """N message gần nhất, cắt sao cho không vỡ cặp tool_call <-> kết quả.

        start_on="human" là chỗ quan trọng: cắt bừa có thể để lại ToolMessage mồ
        côi, hoặc AIMessage có tool_calls mà thiếu kết quả — Gemini trả lỗi 400
        chứ không phải trả lời cụt.
        """
        return trim_messages(
            state.get("messages", []),
            max_tokens=self.max_history_messages,
            token_counter=len,  # đếm số message, không phải token
            strategy="last",
            start_on="human",
            include_system=False,
        )

    async def call_model(self, state: GraphState) -> dict:
        recent_messages = self._recent_history(state)
        # Nhánh này cũng cần mốc ngày như nhánh trích: không có thì "20/7" của
        # người dùng bị model gán một năm tự nghĩ ra, tra DB không ra gì.
        today = now_local().date()
        system_prompt = load_prompt(
            "agent_system",
            TODAY=today.isoformat(),
            TODAY_WEEKDAY=weekday_vi(today),
        )
        response = await self.qa_assistant.ainvoke(
            {
                "system": [SystemMessage(content=system_prompt)],
                "messages": recent_messages,
            }
        )

        # Xoá hẳn phần đã rơi ra ngoài cửa sổ khỏi state. Chỉ cắt lúc gửi thôi
        # thì chưa đủ: LangGraph vẫn ghi nguyên list vào checkpoint mỗi lượt,
        # thread không bao giờ đổi nên nó phình mãi.
        kept_ids = {message.id for message in recent_messages}
        deletions = [
            RemoveMessage(id=message.id)
            for message in state.get("messages", [])
            if message.id not in kept_ids
        ]
        if deletions:
            log.info("HISTORY: bỏ %d message cũ khỏi state", len(deletions))

        return {"messages": [*deletions, response]}

    async def call_tools(self, state: GraphState) -> dict:
        last_message = state["messages"][-1]
        tool_messages = []
        for tool_call in last_message.tool_calls:
            tool = self._tools_by_name[tool_call["name"]]
            try:
                result = await tool.ainvoke(tool_call["args"])
            except Exception as exc:
                log.exception("Tool %s lỗi", tool_call["name"])
                result = f"Lỗi khi tra cứu: {exc}"
            tool_messages.append(
                ToolMessage(
                    content=str(result),
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )
            )
        return {
            "messages": tool_messages,
            "tool_call_rounds": state.get("tool_call_rounds", 0) + 1,
        }

    def route_agent(self, state: GraphState) -> str:
        last_message = state["messages"][-1]
        wants_tools = bool(getattr(last_message, "tool_calls", None))
        rounds_used = state.get("tool_call_rounds", 0)
        if wants_tools and rounds_used < self.max_tool_rounds:
            return "tools"
        return END

    async def answer(self, state: GraphState) -> dict:
        """Gửi câu trả lời cuối của agent về Telegram.

        Hết max_tool_rounds mà LLM vẫn chỉ trả tool_calls thì content rỗng —
        vẫn phải nói gì đó, im lặng trông như bot chết.
        """
        last_message = state["messages"][-1]
        # Kiểm content trước rồi mới str(): content rỗng của Gemini có thể là []
        # chứ không phải "", str([]) ra "[]" và người dùng nhận đúng hai ký tự đó.
        text = str(last_message.content) if last_message.content else no_answer_text()
        await send_message(state["chat_id"], text)
        return {}

    # ------------------------------------------------------------------
    # Dựng graph
    # ------------------------------------------------------------------

    def route_entry(self, state: GraphState) -> Literal["report", "qa"]:
        """Đặt cạnh route_after_confirm / route_agent cho cùng một họ tên."""
        return "report" if state.get("raw_report_text") else "qa"

    def _with_tracing(self, graph):
        """Gắn Langfuse callback ở tầng graph.

        Bind ở đây thay vì ở từng nơi gọi để mọi lời gọi đều được trace: webhook,
        polling, hay script test gọi graph.ainvoke() trực tiếp. Chưa cấu hình key
        thì trả graph nguyên vẹn, không raise.
        """
        if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
            return graph

        try:
            from langfuse.langchain import CallbackHandler
        except ImportError as exc:
            # Hai nguyên nhân khác nhau, cùng rơi vào đây vì ModuleNotFoundError
            # là subclass của ImportError: (1) chưa cài langfuse; (2) đã cài
            # langfuse nhưng thiếu gói `langchain` — bản thân CallbackHandler
            # import từ đó, langchain-core không đủ. In nguyên văn lỗi để phân
            # biệt được, thay vì đoán mò một trong hai.
            log.warning("Bỏ qua Langfuse tracing: %s", exc)
            return graph

        if base_url := os.getenv("LANGFUSE_BASE_URL"):
            os.environ.setdefault("LANGFUSE_HOST", base_url)

        log.info("Langfuse tracing đã bật")
        return graph.with_config({"callbacks": [CallbackHandler()]})

    def build_graph(self):
        builder = StateGraph(GraphState)

        # Nhánh báo cáo
        builder.add_node("extract_tasks", self.extract_tasks)
        builder.add_node("ask_confirm", self.ask_confirm)
        builder.add_node("read_decision", self.read_decision)
        builder.add_node("save_to_db", self.save_to_db)

        # Nhánh hỏi đáp
        builder.add_node("agent", self.call_model)
        builder.add_node("tools", self.call_tools)
        builder.add_node("answer", self.answer)

        # Cả ba chỗ rẽ nhánh dùng chung một API, chỉ khác điểm xuất phát:
        # START / ask_confirm / agent. set_conditional_entry_point() làm đúng
        # việc này nhưng là API riêng cho mỗi START, đọc thành ngoại lệ.
        builder.add_conditional_edges(
            START, self.route_entry, {"report": "extract_tasks", "qa": "agent"}
        )

        builder.add_edge("extract_tasks", "ask_confirm")
        builder.add_edge("ask_confirm", "read_decision")
        builder.add_conditional_edges(
            "read_decision",
            self.route_after_confirm,
            {
                "save_to_db": "save_to_db",
                "extract_tasks": "extract_tasks",
                "ask_confirm": "ask_confirm",
                END: END,
            },
        )
        builder.add_edge("save_to_db", END)

        builder.add_conditional_edges(
            "agent", self.route_agent, {"tools": "tools", END: "answer"}
        )
        builder.add_edge("tools", "agent")
        builder.add_edge("answer", END)

        return self._with_tracing(builder.compile(checkpointer=self.checkpointer))
