"""Nối dây graph client.

    START → reset → identify_project ─ có dự án + có câu hỏi ─→ agent
                           │                                      │
                           └─ còn lại ──→ respond → END           ├─ có tool_calls → tools → agent
                                                                  └─ hết tool_calls → compose
                                                                                 │
                                                  ┌── "thieu" ──────────────────┤
                                                  └─→ agent          "dat" ─────→ respond → END

HAI vòng lặp con nằm TRONG tool, graph này không biết chúng tồn tại — thêm một
tool là thêm một phần tử vào `CLIENT_TOOLS`, không đụng một cạnh nào ở đây:
    search_docs   graph_client/subgraph/       tra cứu, tự viết lại truy vấn
    query_data    graph_client/subgraph_sql/   sinh SQL, tự sửa khi Postgres nổ

Năm cái trần, mỗi cái chặn một kiểu chạy mãi khác nhau — thiếu bất kỳ cái nào là
có một đường vòng vô hạn:
    max_tool_rounds    agent gọi tool bao nhiêu lượt trong một lần hỏi
    max_iterations     TỔNG số lần vào node agent, kể cả lần compose đá ngược về
    max_revise         số lần compose được phép đá ngược
    max_attempts       (trong subgraph)     số lần viết lại truy vấn
    max_sql_attempts   (trong subgraph_sql) số lần sinh/sửa SQL

Dựng bằng CLASS vì luồng này có BỐN chỗ gọi LLM với bốn khối cấu hình khác nhau.
Viết bằng hàm module-level thì bốn model đó thành bốn biến toàn cục, dựng lúc
import, không nhìn ra cái nào của node nào. Thành thuộc tính có tên nói rõ việc
nó làm (`project_identifier`, `qa_agent`, `answer_composer`) thì đọc
`__init__` là thấy hết bản đồ chi phí của một lượt hỏi.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from TAR_agent.graph_client.helpers import grounding
from TAR_agent.graph_client.nodes.compose import (
    Compose,
    render_tool_results,
    tool_payload,
)
from TAR_agent.graph_client.nodes.identify_project import (
    NO_PROJECTS_REPLY,
    ProjectPick,
    ask_what_about,
    describe_current,
    describe_projects,
    last_question,
    validate_pick,
)
from TAR_agent.graph_client.nodes.reset import reset
from TAR_agent.graph_client.nodes.respond import (
    HEDGE,
    answer_event,
    failed_event,
    outgoing_text,
)
from TAR_agent.graph_client.state import ClientState
from TAR_agent.graph_client.tools import CLIENT_TOOLS
from TAR_agent.utils.config import create_google_genai, load_config, load_prompt, now_local
from TAR_agent.utils.projects import list_projects

log = logging.getLogger(__name__)


class ClientGraph:
    def __init__(self, checkpointer: BaseCheckpointSaver) -> None:
        self.checkpointer = checkpointer
        config = load_config()
        models = config["models"]
        agent_cfg = config["agent"]

        self.tools = CLIENT_TOOLS
        self.max_tool_rounds = agent_cfg["max_tool_rounds"]
        self.max_history_messages = agent_cfg["max_history_messages"]
        self.max_iterations = agent_cfg["max_iterations"]
        self.max_revise = agent_cfg["max_revise"]

        # System prompt truyền lúc gọi chứ không nằm trong model, nên dựng sẵn được.
        self.project_identifier = create_google_genai(
            models["identify"], output_schema=ProjectPick
        )
        self.qa_agent = create_google_genai(models["client"], tools=self.tools)
        # Bản KHÔNG bind tool, dùng khi chạm trần max_tool_rounds. Cắt cụt vòng
        # lặp bằng cách nhảy thẳng sang compose thì lịch sử còn lại một AIMessage
        # mang tool_calls không có ToolMessage trả lời — Gemini trả 400 ở lượt
        # SAU, không phải lượt gây ra, nên gần như không lần ra được.
        self.qa_agent_final = create_google_genai(models["client"])
        self.answer_composer = create_google_genai(
            models["compose"], output_schema=Compose
        )

    async def _reset(self, state: ClientState) -> dict:
        return reset(state)

    async def _identify_project(self, state: ClientState) -> dict:
        """Chọn dự án, tách câu hỏi, và ghi câu hỏi vào băng làm việc.

        Chạy ở MỌI lượt, không có nhánh tắt: một nguồn duy nhất quyết định
        `project_id`. Cái giá là +1 lượt LLM (~1-2s) cho cả những tin nhắn hiển
        nhiên như "cảm ơn"; đổi lại không có hai đường sinh ra hai kết luận khác
        nhau về việc người dùng đang hỏi dự án nào.
        """
        chat_history = list(state.get("chat_history") or [])
        question_raw = last_question(chat_history)

        projects = await list_projects()
        if not projects:
            return {
                "question_raw": question_raw,
                "project_id": None,
                "project_name": None,
                "reply": NO_PROJECTS_REPLY,
            }

        system = load_prompt(
            "client_system/identify_project",
            PROJECTS=describe_projects(projects),
            CURRENT=describe_current(
                state.get("project_id"), state.get("project_name")
            ),
        )
        try:
            pick: ProjectPick = await self.project_identifier.ainvoke(
                {
                    "system": [SystemMessage(system)],
                    "messages": chat_history[-self.max_history_messages :],
                }
            )
        except Exception:
            log.exception("identify_project thất bại")
            return {"question_raw": question_raw, "project_id": None, "error": "llm_failed"}

        project_id, project_name = validate_pick(pick, projects)
        log.info("Dự án của lượt này: %s (%s)", project_name, project_id)

        # Luật 8: chốt được dự án nhưng tin nhắn chưa hỏi gì ("cho mình hỏi về
        # BTC đi"). Vào `agent` lúc này thì nó phải tự bịa ra một truy vấn, tra
        # về 5 đoạn bất kỳ, rồi compose đổ cả dự án ra màn hình — hai chục giây
        # cho một câu không ai hỏi. GIỮ `project_id` để lượt sau người dùng gõ
        # cộc lốc là hỏi tiếp được ngay.
        if project_id is not None and pick.needs_question:
            log.info("Chưa có câu hỏi để tra — hỏi lại thay vì tra cứu")
            return {
                "question_raw": question_raw,
                "project_id": project_id,
                "project_name": project_name,
                "remaining_question": None,
                "reply": pick.reply or ask_what_about(project_name or ""),
            }

        if project_id is None:
            # LLM đã trả lời thẳng (luật 5/6/7), hoặc bịa id và bị chặn ở
            # validate_pick — cả hai đều đi ra bằng `respond`, không vào agent.
            return {
                "question_raw": question_raw,
                "project_id": None,
                "project_name": None,
                "reply": pick.reply or "Bạn muốn hỏi về dự án nào?",
            }

        return {
            "question_raw": question_raw,
            "project_id": project_id,
            "project_name": project_name,
            "remaining_question": pick.remaining_question,
            "reply": None,
            # NGUYÊN VĂN chứ không phải remaining_question: mã hiệu và tên riêng
            # phải tới được BM25 đúng như người dùng gõ. remaining_question chỉ
            # là gợi ý trọng tâm, và nó nằm trong system prompt.
            "messages": [HumanMessage(question_raw)],
        }

    async def _agent(self, state: ClientState) -> dict:
        rounds = state.get("tool_call_rounds", 0)
        missing = state.get("missing") or ""
        system = load_prompt(
            "client_system/system",
            TODAY=now_local().strftime("%d/%m/%Y"),
            PROJECT=state.get("project_name") or "",
            FOCUS=state.get("remaining_question") or state.get("question_raw") or "",
            MISSING=(
                f"## Vòng trước còn thiếu\n\n{missing}\n\nTra bổ sung đúng phần này."
                if missing
                else ""
            ),
        )

        # Chạm trần thì đổi model chứ không cắt vòng lặp — xem chú thích ở
        # `qa_agent_final` trong __init__.
        model = self.qa_agent if rounds < self.max_tool_rounds else self.qa_agent_final
        try:
            reply = await model.ainvoke(
                {
                    "system": [SystemMessage(system)],
                    "messages": self._trim(state.get("messages") or []),
                }
            )
        except Exception:
            log.exception("Node agent thất bại")
            return {"error": "llm_failed"}

        update: dict[str, Any] = {
            "messages": [reply],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
        if getattr(reply, "tool_calls", None):
            update["tool_call_rounds"] = rounds + 1
        return update

    async def _compose(self, state: ClientState) -> dict:
        """Soạn câu trả lời, rồi cưỡng chế bằng `grounding.check`.

        `verdict` của model một mình không đáng tin — nó vừa viết xong rồi tự
        chấm bản của chính nó. `grounding.check` mới là thứ chặn được, và nó
        GHI ĐÈ `verdict` chứ không chỉ cảnh báo.
        """
        messages = state.get("messages") or []
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        question = state.get("question_raw") or ""
        system = load_prompt(
            "client_system/compose",
            TODAY=now_local().strftime("%d/%m/%Y"),
            PROJECT=state.get("project_name") or "",
            QUESTION=question,
        )
        try:
            result: Compose = await self.answer_composer.ainvoke(
                {
                    "system": [SystemMessage(system)],
                    "messages": [HumanMessage(render_tool_results(tool_messages))],
                }
            )
        except Exception:
            log.exception("Node compose thất bại")
            return {"error": "llm_failed"}

        verdict = "dat" if result.verdict == "dat" else "thieu"
        missing = result.missing or ""

        problem = grounding.check(result.answer, tool_messages, question)
        if problem:
            log.warning("Câu trả lời không bám nguồn: %s", problem)
            verdict, missing = "thieu", problem

        update: dict[str, Any] = {
            "answer": result.answer,
            "verdict": verdict,
            "missing": missing,
        }
        # Tăng Ở ĐÂY chứ không ở router: router của LangGraph chỉ chọn đường,
        # nó không ghi được state. `_after_compose` vì thế so bằng `>`, để số
        # lần thật sự quay lại agent đúng bằng `max_revise`.
        if verdict != "dat":
            update["revise_count"] = state.get("revise_count", 0) + 1
        return update

    async def _respond(self, state: ClientState) -> dict:
        if state.get("error"):
            return {"outbox": [failed_event(str(state["error"]))]}

        text = outgoing_text(state)
        if not text:
            log.warning("Lượt kết thúc mà không có chữ nào để gửi")
            return {"outbox": [failed_event("empty_answer")]}

        # Có `answer` mà verdict vẫn không "dat" = đi ra bằng đường chạm trần.
        # Gửi kèm câu rào còn hơn im lặng, nhưng phải nói rõ.
        if state.get("answer") and state.get("verdict") != "dat":
            text += HEDGE

        return {
            "outbox": [answer_event(text, state.get("project_name"))],
            "chat_history": [AIMessage(text)],
        }

    def _after_identify(self, state: ClientState) -> str:
        """`reply` khác None = lượt này đã có chữ để gửi, không còn gì để tra.

        Kiểm `reply` TRƯỚC `project_id`: nhánh luật 8 chốt được dự án mà vẫn kết
        thúc sớm, nên chỉ nhìn `project_id` là đá thẳng nó vào vòng tra cứu.
        `reset` dọn `reply` đầu mỗi lượt, nên đây luôn là `reply` của lượt này.
        """
        if state.get("reply"):
            return "respond"
        return "agent" if state.get("project_id") else "respond"

    def _after_agent(self, state: ClientState) -> str:
        # Agent hỏng thì đi thẳng ra cửa. Cho qua `compose` là đốt thêm một lượt
        # LLM đắt tiền để soạn câu trả lời từ một băng làm việc không có gì.
        if state.get("error"):
            return "respond"
        last = (state.get("messages") or [])[-1:]
        if last and getattr(last[0], "tool_calls", None):
            return "tools"
        return "compose"

    def _after_compose(self, state: ClientState) -> str:
        if state.get("error"):
            return "respond"
        if state.get("verdict") == "dat":
            return "respond"
        if state.get("revise_count", 0) > self.max_revise:
            return "respond"
        if state.get("iteration_count", 0) >= self.max_iterations:
            return "respond"
        # Kho thật sự không có dữ liệu thì đá về agent chỉ đốt thêm hai vòng để
        # ra đúng câu trả lời cũ.
        if self._all_tools_empty(state):
            log.info("Mọi lượt tra đều rỗng — không đá ngược về agent")
            return "respond"
        return "agent"

    def _trim(self, messages: list[Any]) -> list[Any]:
        """Cắt lịch sử băng làm việc, rồi bỏ ToolMessage đứng đầu.

        Cắt trần trụi `[-n:]` có thể để lại một ToolMessage mở đầu mà AIMessage
        sinh ra nó đã bị cắt mất. Gemini từ chối cả lượt gọi khi thấy như vậy.
        """
        window = messages[-self.max_history_messages :]
        start = 0
        while start < len(window) and isinstance(window[start], ToolMessage):
            start += 1
        return window[start:]

    def _all_tools_empty(self, state: ClientState) -> bool:
        """Mọi lời gọi tool của LƯỢT NÀY đều trả `status: "empty"`.

        `messages` đã được `reset` dọn sạch đầu lượt nên ở đây chỉ có ToolMessage
        của chính lượt này — đó là lý do việc dọn kia không phải chuyện dọn dẹp
        cho gọn mắt.
        """
        tool_messages = [
            m for m in state.get("messages") or [] if isinstance(m, ToolMessage)
        ]
        if not tool_messages:
            return False
        # Không đọc được payload -> `tool_payload` trả None -> KHÔNG coi là rỗng.
        # Nhánh này chỉ để tắt một vòng lặp vô ích, nên nó phải im lặng khi
        # không chắc, chứ không được tự chốt "kho không có".
        payloads = [tool_payload(m) for m in tool_messages]
        return all(p is not None and p.get("status") == "empty" for p in payloads)

    def build(self):
        builder = StateGraph(ClientState)
        builder.add_node("reset", self._reset)
        builder.add_node("identify_project", self._identify_project)
        builder.add_node("agent", self._agent)
        builder.add_node("tools", ToolNode(self.tools))
        builder.add_node("compose", self._compose)
        builder.add_node("respond", self._respond)

        builder.add_edge(START, "reset")
        builder.add_edge("reset", "identify_project")
        builder.add_conditional_edges(
            "identify_project",
            self._after_identify,
            {"agent": "agent", "respond": "respond"},
        )
        builder.add_conditional_edges(
            "agent",
            self._after_agent,
            {"tools": "tools", "compose": "compose", "respond": "respond"},
        )
        builder.add_edge("tools", "agent")
        builder.add_conditional_edges(
            "compose", self._after_compose, {"agent": "agent", "respond": "respond"}
        )
        builder.add_edge("respond", END)
        return builder.compile(checkpointer=self.checkpointer)


def build_client_graph(checkpointer: BaseCheckpointSaver):
    """Giữ hàm này để app/main.py gọi giống build_admin_graph."""
    return ClientGraph(checkpointer).build()
