"""Nối dây graph client.

    START → reset → identify_project ─ có dự án ─→ agent ─ không cần tra ─→ respond
                           │                        │  ▲                      ▲
                           └─ còn lại ──→ respond   │  └── retrieve ←─┐       │
                                                    │        │        │       │
                                        "compose" ──┴────────┘        │       │
                                                    ▼                 │       │
                                                 compose ─ thiếu ──────┘       │
                                                    └───── đạt ────────────────┘

Node `agent` chạy HAI chế độ, phân biệt bằng việc lượt này đã có kết quả tra
chưa (xem `nodes/agent.py`):

    lần đầu vào (chưa tra)  QUYẾT ĐỊNH  tra kho, hay trả lời thẳng bằng hội thoại
    vào từ `retrieve`       CHỌN LỌC    giữ lại đúng phần trả lời được câu hỏi

Nó KHÔNG bind tool. Việc chọn nguồn đã bỏ hẳn — `retrieve_all` chạy song song cả
hai nhánh. Hai việc còn lại là thứ bản trước thiếu: một đường ra không đi qua
tra cứu, và một chỗ đọc câu hỏi cạnh dữ liệu (SQL khớp mờ trả 27 dòng thì
compose in cả 27, đúng từng chữ nhưng đó là kết quả truy vấn dán vào tin nhắn).

Chi phí: hai lượt LLM thêm cho một câu hỏi bình thường. Câu KHÔNG cần tra thì
ngược lại — rẻ hơn hẳn, vì nó ra thẳng ở `agent`.

`retrieve` không gọi LLM. Nó gọi `retrieve_all`, hàm chạy song song hai subgraph
mà graph này không biết chúng tồn tại:
    subgraph/       tra tài liệu, tự viết lại truy vấn khi hụt
    subgraph_sql/   sinh SQL, tự sửa khi Postgres nổ

Bốn cái trần, mỗi cái chặn một kiểu chạy mãi khác nhau:
    max_iterations     TỔNG số lần vào `retrieve`, kể cả lần compose đá ngược về
    max_revise         số lần compose được phép đá ngược
    max_attempts       (trong subgraph)     số lần viết lại truy vấn
    max_sql_attempts   (trong subgraph_sql) số lần sinh/sửa SQL

Vòng revise của `compose` đi thẳng về `retrieve`, KHÔNG về `agent`: state lúc đó
đã có ToolMessage nên `agent` sẽ vào chế độ chọn lọc và chọn lại đúng lô cũ —
một lượt LLM để ra đúng kết quả cũ.

Dựng bằng CLASS vì bốn chỗ gọi LLM ở đây dùng bốn khối cấu hình khác nhau; đọc
`__init__` là thấy hết bản đồ chi phí của một lượt hỏi.
"""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from TAR_agent.graph_client.helpers import grounding
from TAR_agent.graph_client.nodes.agent import (
    EvidenceSelection,
    RetrievalDecision,
    count_evidence,
    describe_rows,
    filter_evidence,
    number_evidence,
)
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
from TAR_agent.graph_client.tools import retrieve_all
from TAR_agent.utils.config import create_google_genai, load_config, load_prompt, now_local
from TAR_agent.utils.projects import list_projects
from TAR_agent.utils.timing import timed_node

log = logging.getLogger(__name__)


class ClientGraph:
    def __init__(self, checkpointer: BaseCheckpointSaver) -> None:
        self.checkpointer = checkpointer
        config = load_config()
        models = config["models"]
        agent_cfg = config["agent"]

        self.max_history_messages = agent_cfg["max_history_messages"]
        self.max_iterations = agent_cfg["max_iterations"]
        self.max_revise = agent_cfg["max_revise"]

        # BỐN model, đúng bằng số chỗ gọi LLM ở tầng này. `decide` và `select` là
        # hai CHẾ ĐỘ của cùng node `agent` nhưng hai khối cấu hình riêng: quyết
        # định đọc một câu và trả một cờ, chọn lọc đọc vài chục dòng dữ liệu.
        self.project_identifier = create_google_genai(
            models["identify"], output_schema=ProjectPick
        )
        self.decider = create_google_genai(
            models["decide"], output_schema=RetrievalDecision
        )
        self.selector = create_google_genai(
            models["select"], output_schema=EvidenceSelection
        )
        self.answer_composer = create_google_genai(
            models["compose"], output_schema=Compose
        )

    async def _reset(self, state: ClientState) -> dict:
        return reset(state)

    async def _identify_project(self, state: ClientState) -> dict:
        """Chọn dự án, tách câu hỏi, và ghi câu hỏi vào băng làm việc.

        Chạy ở MỌI lượt, không có nhánh tắt: một nguồn duy nhất quyết định
        `project_id`. Cái giá là +1 lượt LLM cho cả những tin nhắn hiển nhiên như
        "cảm ơn"; đổi lại không có hai đường sinh ra hai kết luận khác nhau.
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
            return {
                "question_raw": question_raw,
                "project_id": None,
                "error": "llm_failed",
            }

        project_id, project_name = validate_pick(pick, projects)
        log.info("Dự án của lượt này: %s (%s)", project_name, project_id)

        # Luật 8: chốt được dự án nhưng tin nhắn chưa hỏi gì ("cho mình hỏi về
        # BTC đi"). Vào `agent` lúc này thì nó phải tự bịa ra một truy vấn rồi
        # compose đổ cả dự án ra màn hình. GIỮ `project_id` để lượt sau người
        # dùng gõ cộc lốc là hỏi tiếp được ngay.
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
            # validate_pick — cả hai đều đi ra bằng `respond`.
            return {
                "question_raw": question_raw,
                "project_id": None,
                "project_name": None,
                "reply": pick.reply or "Bạn muốn hỏi về dự án nào?",
            }

        # KHÔNG ghi câu hỏi vào `messages`: `messages` giờ chỉ chứa kết quả tra,
        # còn câu hỏi tới hai chế độ của `agent` bằng đường khác (`question_raw`
        # trong system prompt, `chat_history` trong messages).
        return {
            "question_raw": question_raw,
            "project_id": project_id,
            "project_name": project_name,
            "remaining_question": pick.remaining_question,
            "reply": None,
        }

    async def _agent(self, state: ClientState) -> dict:
        """Một node, hai chế độ. Chưa tra thì QUYẾT ĐỊNH, tra rồi thì CHỌN LỌC.

        Phân biệt bằng `messages` chứ không bằng một cờ trong state: `reset` dọn
        sạch `messages` đầu mỗi lượt nên "lượt này đã tra chưa" đọc thẳng ra
        được từ đó. Một cờ riêng là thêm một thứ có thể lệch với sự thật.
        """
        tool_messages = [
            m for m in state.get("messages") or [] if isinstance(m, ToolMessage)
        ]
        if not tool_messages:
            return await self._decide(state)
        return await self._select_evidence(state, tool_messages)

    async def _decide(self, state: ClientState) -> dict:
        """Lượt này có phải đi tra không.

        Hỏng thì MỞ chứ không đóng — cùng nguyên tắc với `grade` và `gen_sql`:
        lỗi mạng mà coi như "không cần tra" thì bot trả lời chay một câu đáng lẽ
        phải tra, và câu đó nghe vẫn rất thật.
        """
        question = (state.get("question_raw") or "").strip()
        system = load_prompt(
            "client_system/agent_decide",
            TODAY=now_local().strftime("%d/%m/%Y"),
            PROJECT=state.get("project_name") or "",
            QUESTION=question,
        )
        chat_history = list(state.get("chat_history") or [])
        try:
            pick: RetrievalDecision = await self.decider.ainvoke(
                {
                    "system": [SystemMessage(system)],
                    "messages": chat_history[-self.max_history_messages :],
                }
            )
        except Exception:
            log.exception("Node agent (quyết định) thất bại — cứ tra")
            return {"agent_action": "retrieve", "query": question}

        # Nói "không tra" mà quên viết câu trả lời thì lượt đó ra `respond` với
        # chuỗi rỗng. Thiếu chữ là quay lại đường tra cứu, không phải im lặng.
        if not pick.needs_retrieval and pick.direct_answer.strip():
            log.info("Không cần tra — trả lời thẳng")
            return {"agent_action": "answer", "reply": pick.direct_answer.strip()}

        query = pick.query.strip() or question
        log.info("Cần tra · truy vấn: %r", query)
        return {"agent_action": "retrieve", "query": query}

    async def _select_evidence(self, state: ClientState, tool_messages: list) -> dict:
        """Giữ lại đúng phần trả lời được câu hỏi, rồi mới đưa cho `compose`.

        Ghi ra `evidence` (khối nguồn ĐÃ lọc) chứ không sửa `messages`:
        `grounding.check` phải chấm câu trả lời theo lô GỐC. Chấm theo lô đã lọc
        thì một dòng bị bỏ nhầm biến con số trong đó thành số "bịa".

        Hỏng thì MỞ: lỗi LLM, hoặc model vứt sạch một lô đang có dữ liệu, đều
        quay về dùng nguyên lô. Giữ thừa thì câu trả lời dài hơn; bỏ hết thì
        compose nói "kho không có" trong khi kho vừa trả về 27 dòng.
        """
        row_count, passage_count = count_evidence(tool_messages)
        full_evidence = render_tool_results(tool_messages)
        if not row_count and not passage_count:
            # Không có gì để chọn — đốt một lượt LLM ở đây là đốt để không đổi
            # được gì.
            return {"agent_action": "compose", "evidence": full_evidence, "outline": ""}

        system = load_prompt(
            "client_system/agent_select",
            TODAY=now_local().strftime("%d/%m/%Y"),
            PROJECT=state.get("project_name") or "",
            QUESTION=state.get("question_raw") or "",
        )
        try:
            selection: EvidenceSelection = await self.selector.ainvoke(
                {
                    "system": [SystemMessage(system)],
                    "messages": [HumanMessage(number_evidence(tool_messages))],
                }
            )
        except Exception:
            log.exception("Node agent (chọn lọc) thất bại — giữ nguyên lô")
            return {"agent_action": "compose", "evidence": full_evidence, "outline": ""}

        # Câu SQL + nguyên văn các dòng đi CÙNG MỘT dòng log với chỉ số giữ lại:
        # nhìn riêng "giữ 0/9 dòng" thì không phân biệt được `select` bỏ nhầm với
        # `gen_sql` khớp nhầm nhóm, mà hai chuyện đó sửa ở hai chỗ khác nhau.
        if not selection.keep_rows and not selection.keep_passages:
            log.warning(
                "Chọn lọc bỏ sạch %d dòng / %d đoạn — giữ nguyên lô\n%s",
                row_count,
                passage_count,
                describe_rows(tool_messages),
            )
            return {"agent_action": "compose", "evidence": full_evidence, "outline": ""}

        kept = filter_evidence(
            tool_messages, selection.keep_rows, selection.keep_passages
        )
        log.info(
            "Chọn lọc: giữ dòng %s/%d, đoạn %s/%d — %s\n%s",
            sorted(selection.keep_rows),
            row_count,
            sorted(selection.keep_passages),
            passage_count,
            selection.outline or "(không có dàn ý)",
            describe_rows(tool_messages),
        )
        return {
            "agent_action": "compose",
            "evidence": render_tool_results(kept),
            "outline": selection.outline.strip(),
        }

    async def _retrieve(self, state: ClientState) -> dict:
        """Tra cả hai nhánh, KHÔNG gọi LLM ở tầng này.

        Kết quả gói thành `ToolMessage` dù không còn tool-calling nào: ba chỗ đọc
        nó — `render_tool_results`, `grounding.check`, `_all_tools_empty` — đều
        đã nhận `ToolMessage`, và `messages` từ nay không đi tới model nào nên
        Gemini không còn soi cặp AIMessage/ToolMessage phải đi liền nhau.

        Tra bằng `query` của node `agent`; `question_raw` chỉ là đường lui. KHÔNG
        dùng `remaining_question`: mã hiệu và tên riêng ("HM3", "Báo cáo NCKT")
        phải tới được BM25 đúng như người dùng gõ, mà bước tách tên dự án có thể
        làm rụng chúng.

        Vòng revise gửi kèm `missing` — nó là thứ DUY NHẤT khác giữa hai vòng,
        không có nó thì hai subgraph trả về đúng kết quả cũ.
        """
        question = (state.get("query") or state.get("question_raw") or "").strip()
        if missing := (state.get("missing") or "").strip():
            question = f"{question}\n\nVòng trước còn thiếu: {missing}"

        payload = await retrieve_all(question, state["project_id"])

        return {
            "messages": [
                ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                    tool_call_id=f"retrieve_all-{state.get('iteration_count', 0)}",
                    name="retrieve_all",
                )
            ],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    async def _compose(self, state: ClientState) -> dict:
        """Soạn câu trả lời, rồi cưỡng chế bằng `grounding.check`.

        `verdict` của model một mình không đáng tin — nó vừa viết xong rồi tự
        chấm bản của chính nó. `grounding.check` GHI ĐÈ `verdict` chứ không chỉ
        cảnh báo.

        Nguồn đưa cho model là `evidence` (bản ĐÃ LỌC), còn `grounding.check`
        chấm theo `tool_messages` GỐC: lọc là để câu trả lời đúng trọng tâm,
        không phải để thu hẹp thứ được coi là có thật.
        """
        messages = state.get("messages") or []
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        question = state.get("question_raw") or ""
        evidence = (state.get("evidence") or "").strip() or render_tool_results(
            tool_messages
        )
        system = load_prompt(
            "client_system/compose",
            TODAY=now_local().strftime("%d/%m/%Y"),
            PROJECT=state.get("project_name") or "",
            QUESTION=question,
            OUTLINE=(state.get("outline") or "").strip() or "(không có)",
        )
        try:
            result: Compose = await self.answer_composer.ainvoke(
                {
                    "system": [SystemMessage(system)],
                    "messages": [HumanMessage(evidence)],
                }
            )
        except Exception:
            log.exception("Node compose thất bại")
            return {"error": "llm_failed"}

        verdict = "ok" if result.verdict == "ok" else "insufficient"
        missing = result.missing or ""

        problem = grounding.check(result.answer, tool_messages, question)
        if problem:
            log.warning("Câu trả lời không bám nguồn: %s", problem)
            verdict, missing = "insufficient", problem

        update: dict[str, Any] = {
            "answer": result.answer,
            "verdict": verdict,
            "missing": missing,
        }
        # Tăng Ở ĐÂY chứ không ở router: router của LangGraph chỉ chọn đường, nó
        # không ghi được state. `_after_compose` vì thế so bằng `>`.
        if verdict != "ok":
            update["revise_count"] = state.get("revise_count", 0) + 1
        return update

    async def _respond(self, state: ClientState) -> dict:
        if state.get("error"):
            return {"outbox": [failed_event(str(state["error"]))]}

        text = outgoing_text(state)
        if not text:
            log.warning("Lượt kết thúc mà không có chữ nào để gửi")
            return {"outbox": [failed_event("empty_answer")]}

        # Có `answer` mà verdict vẫn không "ok" = đi ra bằng đường chạm trần.
        # Gửi kèm câu rào còn hơn im lặng, nhưng phải nói rõ.
        if state.get("answer") and state.get("verdict") != "ok":
            text += HEDGE

        return {
            "outbox": [answer_event(text, state.get("project_name"))],
            "chat_history": [AIMessage(text)],
        }

    def _after_identify(self, state: ClientState) -> str:
        """`reply` khác None = lượt này đã có chữ để gửi, không còn gì để tra.

        Kiểm `reply` TRƯỚC `project_id`: nhánh luật 8 chốt được dự án mà vẫn kết
        thúc sớm, nên chỉ nhìn `project_id` là đá thẳng nó vào vòng tra cứu.
        """
        if state.get("reply"):
            return "respond"
        return "agent" if state.get("project_id") else "respond"

    def _after_agent(self, state: ClientState) -> str:
        """Đọc `agent_action` chứ không đoán lại từ `messages`.

        Suy lại từ state ở đây là hai chỗ cùng quyết một việc, và chúng sẽ lệch
        nhau ở đúng ca hiếm không ai nghĩ tới. Mặc định là `retrieve`: giá trị lạ
        hoặc thiếu thì đi tra, chứ không ra thẳng `respond` với câu trả lời rỗng.
        """
        if state.get("error"):
            return "respond"
        action = state.get("agent_action")
        if action == "answer":
            return "respond"
        if action == "compose":
            return "compose"
        return "retrieve"

    def _after_compose(self, state: ClientState) -> str:
        if state.get("error"):
            return "respond"
        if state.get("verdict") == "ok":
            return "respond"
        if state.get("revise_count", 0) > self.max_revise:
            return "respond"
        if state.get("iteration_count", 0) >= self.max_iterations:
            return "respond"
        # Kho thật sự không có dữ liệu thì tra lại chỉ đốt thêm một vòng để ra
        # đúng câu trả lời cũ.
        if self._all_tools_empty(state):
            log.info("Mọi lượt tra đều rỗng — không tra lại")
            return "respond"
        return "retrieve"

    def _all_tools_empty(self, state: ClientState) -> bool:
        """Mọi lượt tra của LƯỢT NÀY đều trả `status: "empty"`.

        `retrieve_all` chỉ đặt "empty" khi CẢ HAI nhánh không có gì, nên nhánh
        tắt này không cắt nhầm lượt còn cứu được.

        Payload không đọc được -> `tool_payload` trả None -> KHÔNG coi là rỗng:
        nhánh này chỉ để tắt một vòng lặp vô ích nên nó phải im lặng khi không
        chắc, không được tự chốt "kho không có".
        """
        tool_messages = [
            m for m in state.get("messages") or [] if isinstance(m, ToolMessage)
        ]
        if not tool_messages:
            return False
        payloads = [tool_payload(m) for m in tool_messages]
        return all(p is not None and p.get("status") == "empty" for p in payloads)

    def build(self):
        builder = StateGraph(ClientState)
        # Mọi node đi qua `timed_node`: đây là chỗ DUY NHẤT biết về đo đạc, thân
        # node không có dòng nào. Tên node cũng là nhãn trong log.
        for name, node in {
            "reset": self._reset,
            "identify_project": self._identify_project,
            "agent": self._agent,
            "retrieve": self._retrieve,
            "compose": self._compose,
            "respond": self._respond,
        }.items():
            builder.add_node(name, timed_node(name, node))

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
            {"retrieve": "retrieve", "compose": "compose", "respond": "respond"},
        )
        # `retrieve` quay lại `agent` — lần này nó vào chế độ CHỌN LỌC, vì lượt
        # đã có ToolMessage.
        builder.add_edge("retrieve", "agent")
        builder.add_conditional_edges(
            "compose",
            self._after_compose,
            {"retrieve": "retrieve", "respond": "respond"},
        )
        builder.add_edge("respond", END)
        return builder.compile(checkpointer=self.checkpointer)


def build_client_graph(checkpointer: BaseCheckpointSaver):
    """Giữ hàm này để app/main.py gọi giống build_admin_graph."""
    return ClientGraph(checkpointer).build()
