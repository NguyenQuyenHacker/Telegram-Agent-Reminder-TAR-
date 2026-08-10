"""Job C: agent xử lý báo cáo và hỏi đáp.

Toàn bộ node là method của ReminderAgent, để mọi thứ một lượt chạy cần —
vai LLM, tool, checkpointer — nằm trong self, khai báo một chỗ ở __init__ thay
vì rải khắp các hàm module-level.

Graph có hai nhánh:
  báo cáo:  extract_tasks -> ask_confirm -(duyệt)-> save_to_db
                                 ^ interrupt, chờ người dùng
  hỏi đáp:  call_model <-> call_tools -> answer
"""

import asyncio
import hashlib
import logging
import os
import re
from datetime import date

from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.core.datetime_utils import now_local, weekday_vi
from app.core.priority import clamp_to_window, interval_for
from app.telegram.messages import (
    abandoned_text,
    extraction_summary_text,
    missing_due_date_prompt,
    no_answer_text,
    saved_text,
)
from app.telegram.sender import send_message
from persistence.models.task import Priority, TaskStatus
from persistence.proc.reports import insert_report
from persistence.proc.tasks import upsert_task
from reminder_agent.config.settings import create_google_genai, load_config
from reminder_agent.prompts.loader import load_prompt
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
        """R7: cùng nhóm + cùng nội dung luôn ra cùng mã, để đồng bộ lại không trùng."""
        collapsed_content = re.sub(r"\s+", " ", content.strip().lower())
        normalized = f"{group.strip().lower()}|{collapsed_content}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    async def extract_tasks(self, state: GraphState) -> dict:
        """R1/R2/R9: trích đầu việc ở khối 'Tiếp theo:', gán mức ưu tiên, không đoán hạn."""
        raw = state.get("raw_report_text") or ""
        edit_request = state.get("edit_request")

        prompt = load_prompt("extract_system", TODAY=now_local().date().isoformat())
        if edit_request:
            prompt += "\n\n" + load_prompt("extract_retry", EDIT_REQUEST=edit_request)

        result = await self.task_extractor.ainvoke(
            {
                "system": [SystemMessage(content=prompt)],
                "messages": [HumanMessage(content=raw)],
            }
        )

        tasks = [t.model_dump() for t in result.tasks]
        log.info("EXTRACT: trích được %d đầu việc", len(tasks))

        # Chỉ lưu báo cáo ở lần trích đầu. Nhánh sửa quay lại chính node này,
        # lưu tiếp là nhân bản cùng một báo cáo trong DB.
        if not edit_request:
            await asyncio.to_thread(insert_report, raw)

        # Gửi bảng ở đây chứ không ở ask_confirm: node có interrupt() sẽ chạy
        # lại từ đầu mỗi lần resume, để send_message trong đó thì người dùng
        # nhận lại bảng cũ mỗi lần họ trả lời.
        await send_message(state["chat_id"], extraction_summary_text(tasks))
        return {"extracted_tasks": tasks, "edit_request": None}

    async def ask_confirm(self, state: GraphState) -> dict:
        """Chốt chặn người duyệt: graph dừng tại đây tới khi người dùng phản hồi."""
        decision = interrupt(
            {"awaiting": "confirm", "tasks": state.get("extracted_tasks", [])}
        )
        status = decision.get("status")
        if status == "abandoned":
            await send_message(state["chat_id"], abandoned_text())
        return {"confirm_status": status, "edit_request": decision.get("edit_request")}

    def route_after_confirm(self, state: GraphState) -> str:
        status = state.get("confirm_status")
        if status == "approved":
            return "save_to_db"
        if status == "edit":
            # Nơi gọi chỉ resume khi đã biết sửa chỗ nào (xem webhooks.py), nên
            # tới đây chắc chắn có edit_request để trích lại
            return "extract_tasks"
        return END

    async def save_to_db(self, state: GraphState) -> dict:
        """R7: upsert theo task_id; R9: hỏi bổ sung hạn đúng một lần."""
        tasks = state.get("extracted_tasks", [])
        if not tasks:
            await send_message(state["chat_id"], abandoned_text())
            return {}

        next_at = clamp_to_window(now_local())
        for t in tasks:
            priority = Priority(t["priority"])
            saved = await asyncio.to_thread(
                upsert_task,
                self.compute_task_id(t["group"], t["content"]),
                t["group"],
                t["content"],
                date.fromisoformat(t["due_date"]) if t.get("due_date") else None,
                priority,
                interval_for(priority, TaskStatus.pending),
                next_at,
            )
            if saved.due_date is None:
                await send_message(state["chat_id"], missing_due_date_prompt(saved))

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
        history = self._recent_history(state)
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
                "messages": history,
            }
        )

        # Xoá hẳn phần đã rơi ra ngoài cửa sổ khỏi state. Chỉ cắt lúc gửi thôi
        # thì chưa đủ: LangGraph vẫn ghi nguyên list vào checkpoint mỗi lượt,
        # thread không bao giờ đổi nên nó phình mãi.
        kept = {m.id for m in history}
        dropped = [
            RemoveMessage(id=m.id) for m in state.get("messages", []) if m.id not in kept
        ]
        if dropped:
            log.info("HISTORY: bỏ %d message cũ khỏi state", len(dropped))

        return {"messages": [*dropped, response]}

    async def call_tools(self, state: GraphState) -> dict:
        last = state["messages"][-1]
        outputs = []
        for call in last.tool_calls:
            tool = self._tools_by_name[call["name"]]
            try:
                result = await tool.ainvoke(call["args"])
            except Exception as exc:
                log.exception("Tool %s lỗi", call["name"])
                result = f"Lỗi khi tra cứu: {exc}"
            outputs.append(
                ToolMessage(content=str(result), name=call["name"], tool_call_id=call["id"])
            )
        return {
            "messages": outputs,
            "tool_call_rounds": state.get("tool_call_rounds", 0) + 1,
        }

    def route_agent(self, state: GraphState) -> str:
        last = state["messages"][-1]
        has_calls = bool(getattr(last, "tool_calls", None))
        rounds = state.get("tool_call_rounds", 0)
        if has_calls and rounds < self.max_tool_rounds:
            return "tools"
        return END

    async def answer(self, state: GraphState) -> dict:
        """Gửi câu trả lời cuối của agent về Telegram.

        Hết max_tool_rounds mà LLM vẫn chỉ trả tool_calls thì content rỗng —
        vẫn phải nói gì đó, im lặng trông như bot chết.
        """
        last = state["messages"][-1]
        # Kiểm content trước rồi mới str(): content rỗng của Gemini có thể là []
        # chứ không phải "", str([]) ra "[]" và người dùng nhận đúng hai ký tự đó.
        text = str(last.content) if last.content else no_answer_text()
        await send_message(state["chat_id"], text)
        return {}

    # ------------------------------------------------------------------
    # Dựng graph
    # ------------------------------------------------------------------

    def _entry(self, state: GraphState) -> str:
        return "extract_tasks" if state.get("raw_report_text") else "agent"

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
        except ImportError:
            log.warning("Đã đặt LANGFUSE_* nhưng chưa cài gói langfuse, bỏ qua tracing")
            return graph

        if base_url := os.getenv("LANGFUSE_BASE_URL"):
            os.environ.setdefault("LANGFUSE_HOST", base_url)

        log.info("Langfuse tracing đã bật")
        return graph.with_config({"callbacks": [CallbackHandler()]})

    def build_graph(self):
        g = StateGraph(GraphState)

        # Nhánh báo cáo
        g.add_node("extract_tasks", self.extract_tasks)
        g.add_node("ask_confirm", self.ask_confirm)
        g.add_node("save_to_db", self.save_to_db)

        # Nhánh hỏi đáp
        g.add_node("agent", self.call_model)
        g.add_node("tools", self.call_tools)
        g.add_node("answer", self.answer)

        g.set_conditional_entry_point(
            self._entry, {"extract_tasks": "extract_tasks", "agent": "agent"}
        )

        g.add_edge("extract_tasks", "ask_confirm")
        g.add_conditional_edges(
            "ask_confirm",
            self.route_after_confirm,
            {"save_to_db": "save_to_db", "extract_tasks": "extract_tasks", END: END},
        )
        g.add_edge("save_to_db", END)

        g.add_conditional_edges("agent", self.route_agent, {"tools": "tools", END: "answer"})
        g.add_edge("tools", "agent")
        g.add_edge("answer", END)

        return self._with_tracing(g.compile(checkpointer=self.checkpointer))
