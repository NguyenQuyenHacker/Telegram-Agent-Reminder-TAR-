"""Job C: agent xử lý báo cáo và hỏi đáp.

Toàn bộ node là method của ReminderAgent, để mọi thứ một lượt chạy cần —
vai LLM, tool, checkpointer — nằm trong self, khai báo một chỗ ở __init__ thay
vì rải khắp các hàm module-level. Ngược lại, mấy bảng tra và hàm ghi DB ở tầng
module (_CONFIRM_ROUTES, _UPDATE_ACTIONS, _apply_*) cố ý nằm ngoài class: chúng
không cần gì trong self, để ngoài thì nhìn một chỗ là thấy hết các nhánh.

Graph có hai nhánh:
  báo cáo:  extract_tasks -> ask_confirm -> read_decision -(duyệt)-> save_to_db
                 ^                ^ interrupt      |
                 +---(sửa)--------+---(chưa rõ)----+
  hỏi đáp:  call_model <-> call_tools -> answer -(có đề xuất)-> ask_update_confirm
                                                                     ^ interrupt
                                                                     |
                              apply_updates <-(duyệt)- read_update_decision

Hai nhánh có cùng một hình dạng ở đoạn cuối vì cùng một nguyên tắc: LLM chỉ đề
xuất, người dùng gật thì code mới ghi DB.
"""

import asyncio
import hashlib
import logging
import os
import re
import uuid
from datetime import date, datetime
from typing import Literal

from langchain_core.messages import (
    AnyMessage,
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
from app.core.priority import clamp_to_window, compute_next_remind_at
from app.core.task_text import normalize_content
from app.telegram.messages import (
    abandoned_text,
    cancel_confirmation_text,
    done_confirmation_text,
    due_updated_text,
    extraction_summary_text,
    missing_due_date_prompt,
    need_reason_text,
    no_answer_text,
    saved_text,
    update_confirm_text,
    update_failed_text,
)
from app.telegram.sender import send_message
from persistence.models.task import Priority, Task
from persistence.proc.groups import get_or_create_group
from persistence.proc.tasks import (
    cancel_task,
    get_task,
    mark_done,
    set_due_date,
    upsert_task,
)
from reminder_agent.config.settings import create_google_genai, load_config
from reminder_agent.prompts.loader import load_prompt
from reminder_agent.utils.intent import parse_free_text_decision
from reminder_agent.utils.schemas import ExtractionResult
from reminder_agent.utils.state import GraphState
from reminder_agent.utils.tools import ALL_TOOLS

log = logging.getLogger(__name__)


def _dated_prompt(name: str, today: date, **variables: str) -> str:
    """Prompt `name` ghép với khối quy ước ngày tháng dùng chung.

    Cả hai nhánh đều cần mốc TODAY: thiếu nó thì "20/7" của người dùng bị model
    gán một năm tự nghĩ ra, tra DB không ra gì.
    """
    return (
        load_prompt(name, TODAY=today.isoformat(), **variables)
        + "\n\n"
        + load_prompt("_date_rules", TODAY=today.isoformat())
    )


# confirm_status -> node kế tiếp. "edit" tới đây chắc chắn có edit_request, vì
# read_decision đã hạ "edit" thiếu lý do xuống "unclear". "unclear" quay lại hỏi
# tiếp chứ KHÔNG kết thúc: bảng đầu việc vẫn đang chờ duyệt.
_CONFIRM_ROUTES = {
    "approved": "save_to_db",
    "edit": "extract_tasks",
    "unclear": "ask_confirm",
    "abandoned": END,
}


def _parse_iso_date(value: str | None) -> date | None:
    """Ngày trong đề xuất -> date. Đề xuất đi qua checkpoint rồi mới tới đây, nên
    vẫn kiểm lại thay vì tin: hỏng một dòng không được làm chết cả node."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _merge_proposals(pending: list[dict], new_proposals: list[dict]) -> list[dict]:
    """Gộp đề xuất mới vào danh sách đang chờ, mỗi việc chỉ giữ đề xuất cuối.

    LLM có thể đề xuất hai lần cho cùng một việc trong một lượt ("xong TB-002"
    rồi tự sửa thành "dời hạn TB-002"). Giữ cả hai là ghi tuần tự hai lệnh lên
    một dòng: mark_done chạy trước, set_due_date sau đó thấy dòng không còn
    pending và trượt — người dùng gật một lần mà nhận về một xác nhận cộng một
    báo lỗi. Bảng xác nhận cũng đọc từ chính danh sách này, nên gộp ở đây là
    thứ người dùng thấy và thứ được ghi luôn khớp nhau.
    """
    by_task = {proposal["task_id"]: proposal for proposal in [*pending, *new_proposals]}
    return list(by_task.values())


def _apply_done(update: dict) -> Task | None:
    return mark_done(update["task_id"])


def _apply_cancel(update: dict) -> Task | None:
    return cancel_task(update["task_id"])


def _apply_reschedule(update: dict) -> Task | None:
    new_due = _parse_iso_date(update.get("new_due_date"))
    if new_due is None:
        # Thiếu hoặc hỏng hạn mới thì KHÔNG ghi: set_due_date(None) sẽ xoá luôn
        # hạn cũ, trong khi bảng xác nhận vừa hứa với người dùng là "đổi hạn".
        log.error("APPLY: đề xuất reschedule không có hạn mới hợp lệ: %r", update)
        return None
    # Mốc nhắc dời ra một nhịp: người dùng vừa đọc xác nhận đổi hạn xong, nhắc
    # lại ngay lượt quét kế tiếp là thừa. Hạn mới không tự đổi nhịp nhắc — nhịp
    # là hằng số cấu hình — nên phải ghi tay ở đây.
    return set_due_date(update["task_id"], new_due, compute_next_remind_at(now_local()))


# action -> (hàm ghi DB, câu xác nhận). Cả ba hàm ghi đều đồng bộ nên
# apply_updates gọi chúng qua to_thread, không hàm nào tự biết mình chạy ở đâu.
_UPDATE_ACTIONS = {
    "done": (_apply_done, done_confirmation_text),
    "cancel": (_apply_cancel, cancel_confirmation_text),
    "reschedule": (_apply_reschedule, due_updated_text),
}


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
    def compute_task_id(group_id: uuid.UUID, content: str) -> str:
        """R7: cùng nhóm + cùng nội dung luôn ra cùng mã, để đồng bộ lại không trùng.

        Băm bản đã gọt "(hạn ...)" chứ không băm nguyên văn: hạn là thuộc tính
        của công việc, không phải căn cước của nó. Băm cả hạn thì báo cáo sửa
        ngày sẽ đẻ ra dòng mới, còn dòng cũ nằm lại nhắc theo hạn đã lỗi thời —
        đúng thứ R7 sinh ra để chống.

        Băm group_id chứ không băm tên nhóm: tên đã được chuẩn hoá một lần khi
        sinh ra id, nên ở đây không phải đoán lại cách chuẩn hoá cho khớp.
        """
        collapsed_content = re.sub(r"\s+", " ", normalize_content(content).lower())
        identity_key = f"{group_id}|{collapsed_content}"
        return hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:16]

    async def extract_tasks(self, state: GraphState) -> dict:
        """R1/R2/R9: trích đầu việc ở khối 'Tiếp theo:', gán mức ưu tiên, không đoán hạn."""
        report_text = state.get("raw_report_text") or ""
        edit_request = state.get("edit_request")

        system_prompt = _dated_prompt("extract_system", now_local().date())
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
        next_node = _CONFIRM_ROUTES.get(status)
        if next_node is None:
            # read_decision luôn ghi một trong bốn status; thiếu nghĩa là node đó
            # không chạy. Dừng hẳn chứ không đoán, còn hơn treo ở interrupt.
            log.error("CONFIRM: status lạ %r chat_id=%s", status, state["chat_id"])
            return END
        return next_node

    async def _upsert_extracted_task(self, task: dict, first_remind_at: datetime) -> Task:
        """Một dòng trích được -> một dòng trong DB. Chạy upsert đồng bộ ở thread khác.

        Dòng nhóm phải có trước vì task.group_id là khoá ngoại trỏ vào nó.
        """
        group = await asyncio.to_thread(get_or_create_group, task["group"])
        return await asyncio.to_thread(
            upsert_task,
            self.compute_task_id(group.group_id, task["content"]),
            group.group_id,
            # Lưu bản đã gọt: hạn chỉ sống ở due_date, mức ưu tiên ở priority.
            normalize_content(task["content"]),
            date.fromisoformat(task["due_date"]) if task.get("due_date") else None,
            Priority(task["priority"]),
            first_remind_at,
        )

    async def save_to_db(self, state: GraphState) -> dict:
        """R7: upsert theo task_id; R9: hỏi bổ sung hạn đúng một lần."""
        tasks = state.get("extracted_tasks", [])
        if not tasks:
            await send_message(state["chat_id"], abandoned_text())
            return {}

        first_remind_at = clamp_to_window(now_local())
        awaiting_due_task_ids = []
        for task in tasks:
            saved_task = await self._upsert_extracted_task(task, first_remind_at)
            if saved_task.due_date is None:
                # Nhớ lại đã hỏi hạn cho việc nào: câu trả lời rơi vào nhánh hỏi
                # đáp, ở đó LLM không có cách nào tự biết "2 ngày nữa" nói về việc gì.
                awaiting_due_task_ids.append(saved_task.task_id)
                await send_message(state["chat_id"], missing_due_date_prompt(saved_task))

        log.info("SAVE: đã lưu %d đầu việc", len(tasks))
        await send_message(state["chat_id"], saved_text(len(tasks)))
        return {"awaiting_due_task_ids": awaiting_due_task_ids}

    # ------------------------------------------------------------------
    # Nhánh hỏi đáp
    # ------------------------------------------------------------------

    def _recent_history(self, state: GraphState) -> list[AnyMessage]:
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

    @staticmethod
    def _load_tasks_missing_due(task_ids: list[str]) -> list[Task]:
        """Việc nào trong danh sách chờ mà vẫn chưa có hạn. Chạy trong to_thread."""
        tasks = (get_task(task_id) for task_id in task_ids)
        return [task for task in tasks if task is not None and task.due_date is None]

    async def _tasks_awaiting_due(self, state: GraphState) -> list[Task]:
        """Việc vừa hỏi hạn mà người dùng chưa trả lời. Không có thì khỏi đụng DB."""
        task_ids = state.get("awaiting_due_task_ids") or []
        if not task_ids:
            return []
        return await asyncio.to_thread(self._load_tasks_missing_due, task_ids)

    @staticmethod
    def _qa_system_prompt(tasks_awaiting_due: list[Task]) -> str:
        """Prompt nhánh hỏi đáp, kèm nhắc việc nào đang chờ hạn nếu có.

        Vừa hỏi hạn cho vài việc thì phải nói cho LLM biết là hỏi việc nào: "3
        ngày nữa" một mình không đủ để nó đoán ra đầu việc nào đang thiếu hạn.
        """
        today = now_local().date()
        prompt = _dated_prompt("agent_system", today, TODAY_WEEKDAY=weekday_vi(today))
        if not tasks_awaiting_due:
            return prompt

        pending_list = ", ".join(
            f"{task.code} {task.content}" for task in tasks_awaiting_due
        )
        return prompt + (
            f"\n\nYou have just asked the user for a deadline for: {pending_list}. "
            "Their next message is most likely that deadline — call "
            "propose_task_update(action='reschedule') for the matching task."
        )

    @staticmethod
    def _history_deletions(
        state: GraphState, kept_messages: list[AnyMessage]
    ) -> list[RemoveMessage]:
        """Xoá hẳn phần đã rơi ra ngoài cửa sổ khỏi state.

        Chỉ cắt lúc gửi thôi thì chưa đủ: LangGraph vẫn ghi nguyên list vào
        checkpoint mỗi lượt, thread không bao giờ đổi nên nó phình mãi.
        """
        kept_ids = {message.id for message in kept_messages}
        deletions = [
            RemoveMessage(id=message.id)
            for message in state.get("messages", [])
            if message.id not in kept_ids
        ]
        if deletions:
            log.info("HISTORY: bỏ %d message cũ khỏi state", len(deletions))
        return deletions

    async def call_model(self, state: GraphState) -> dict:
        recent_messages = self._recent_history(state)
        tasks_awaiting_due = await self._tasks_awaiting_due(state)
        system_prompt = self._qa_system_prompt(tasks_awaiting_due)

        response = await self.qa_assistant.ainvoke(
            {
                "system": [SystemMessage(content=system_prompt)],
                "messages": recent_messages,
            }
        )

        # Danh sách chờ hạn tự cạn: việc đã có hạn thì rơi ra khỏi
        # _tasks_awaiting_due và không bao giờ quay lại. Không cần ai đi dọn.
        return {
            "messages": [*self._history_deletions(state, recent_messages), response],
            "awaiting_due_task_ids": [task.task_id for task in tasks_awaiting_due],
        }

    async def _invoke_tool(self, tool_call: dict) -> object:
        """Chạy một tool_call, trả về giá trị gốc của tool.

        Không để lỗi thoát ra ngoài: LLM phải nhận được lỗi dưới dạng ToolMessage
        thì mới nói lại được với người dùng, còn exception thì cả lượt chết ngang.
        """
        tool_name = tool_call["name"]
        tool = self._tools_by_name.get(tool_name)
        if tool is None:
            # LLM bịa tên tool. Nói thẳng cho nó biết, hơn là ném KeyError ra ngoài.
            log.error("Tool %s không có trong danh sách", tool_name)
            return f"Không có tool tên {tool_name}."

        try:
            return await tool.ainvoke(tool_call["args"])
        except Exception as exc:
            log.exception("Tool %s lỗi", tool_name)
            return f"Lỗi khi tra cứu: {exc}"

    async def call_tools(self, state: GraphState) -> dict:
        last_message = state["messages"][-1]
        tool_messages = []
        proposals = []
        for tool_call in last_message.tool_calls:
            result = await self._invoke_tool(tool_call)
            # Đề xuất được gom ở đây, từ giá trị trả về gốc của tool. Không đọc
            # lại từ câu trả lời của LLM: LLM có thể kể sai mã việc, còn đây là
            # đúng dòng mà propose_task_update đã tra ra.
            if isinstance(result, dict) and result.get("status") == "proposed":
                proposals.append(result)
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
            "pending_updates": _merge_proposals(
                state.get("pending_updates") or [], proposals
            ),
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

        Bảng đề xuất gửi ở ĐÂY chứ không ở ask_update_confirm, đúng lý do đã ghi
        ở ask_confirm: node có interrupt() chạy lại từ đầu mỗi lần resume.
        """
        pending_updates = state.get("pending_updates") or []
        # Có đề xuất thì BỎ luôn lời của LLM: nó chỉ kể lại y hệt những gì bảng
        # đề xuất sắp nói, thành hai tin nhắn trùng nội dung. Bảng mới là bản
        # chuẩn — nó dựng từ giá trị gốc của tool, không qua tay LLM kể lại.
        if pending_updates:
            await send_message(state["chat_id"], update_confirm_text(pending_updates))
            return {}

        last_message = state["messages"][-1]
        # Kiểm content trước rồi mới str(): content rỗng của Gemini có thể là []
        # chứ không phải "", str([]) ra "[]" và người dùng nhận đúng hai ký tự đó.
        text = str(last_message.content) if last_message.content else no_answer_text()
        await send_message(state["chat_id"], text)
        return {}

    def route_after_answer(self, state: GraphState) -> str:
        return "ask_update_confirm" if state.get("pending_updates") else END

    def ask_update_confirm(self, state: GraphState) -> dict:
        """Chốt duyệt cho đề xuất cập nhật. Rỗng hoàn toàn, xem ask_confirm."""
        reply = interrupt(
            {"awaiting": "update", "updates": state.get("pending_updates", [])}
        )
        return {"update_reply": reply}

    async def read_update_decision(self, state: GraphState) -> dict:
        """Đọc "ok" / "thôi" bằng chính LLM đã đọc câu duyệt bảng đầu việc.

        Chưa rõ ý thì BỎ đề xuất chứ không hỏi lại vòng vòng như read_decision:
        dựng lại một đề xuất chỉ tốn một câu người dùng nhắn, trong khi treo ở
        interrupt là nuốt mọi tin nhắn sau đó của họ. Ý muốn sửa cũng vào nhánh
        này — đề xuất chỉ có ok hoặc thôi.
        """
        decision = await parse_free_text_decision(state.get("update_reply") or "")
        approved = decision["status"] == "approved"
        status = "approved" if approved else "abandoned"
        log.info("UPDATE CONFIRM status=%s chat_id=%s", status, state["chat_id"])

        if approved:
            return {"update_status": status}
        # Dọn luôn đề xuất: không duyệt thì nó không được sống sang lượt sau
        await send_message(state["chat_id"], abandoned_text())
        return {"update_status": status, "pending_updates": []}

    def route_after_update(self, state: GraphState) -> str:
        return "apply_updates" if state.get("update_status") == "approved" else END

    async def apply_updates(self, state: GraphState) -> dict:
        """Chỗ DUY NHẤT trong nhánh hỏi đáp được ghi DB, và chỉ sau khi người dùng gật."""
        for update in state.get("pending_updates") or []:
            task_id = update["task_id"]
            action = update["action"]
            handler = _UPDATE_ACTIONS.get(action)
            if handler is None:
                # Không đoán bừa: propose_task_update chỉ sinh ba action đã biết,
                # rơi vào đây là đề xuất hỏng chứ không phải ý mới của người dùng.
                log.error("APPLY: action lạ %r task_id=%s", action, task_id)
                await send_message(state["chat_id"], update_failed_text())
                continue

            write_to_db, confirmation_text = handler
            task = await asyncio.to_thread(write_to_db, update)

            log.info("APPLY action=%s task_id=%s ok=%s", action, task_id, task is not None)
            # Nhắn xác nhận từng thay đổi một, để người dùng soát được cái nào trượt
            await send_message(
                state["chat_id"],
                confirmation_text(task) if task is not None else update_failed_text(),
            )

        return {"pending_updates": []}

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

    def _add_report_branch(self, builder: StateGraph) -> None:
        """Node và cạnh của nhánh báo cáo: trích -> chờ duyệt -> lưu."""
        builder.add_node("extract_tasks", self.extract_tasks)
        builder.add_node("ask_confirm", self.ask_confirm)
        builder.add_node("read_decision", self.read_decision)
        builder.add_node("save_to_db", self.save_to_db)

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

    def _add_qa_branch(self, builder: StateGraph) -> None:
        """Node và cạnh của nhánh hỏi đáp: vòng ReAct -> trả lời -> chờ duyệt -> ghi."""
        builder.add_node("agent", self.call_model)
        builder.add_node("tools", self.call_tools)
        builder.add_node("answer", self.answer)
        builder.add_node("ask_update_confirm", self.ask_update_confirm)
        builder.add_node("read_update_decision", self.read_update_decision)
        builder.add_node("apply_updates", self.apply_updates)

        builder.add_conditional_edges(
            "agent", self.route_agent, {"tools": "tools", END: "answer"}
        )
        builder.add_edge("tools", "agent")
        builder.add_conditional_edges(
            "answer",
            self.route_after_answer,
            {"ask_update_confirm": "ask_update_confirm", END: END},
        )
        builder.add_edge("ask_update_confirm", "read_update_decision")
        builder.add_conditional_edges(
            "read_update_decision",
            self.route_after_update,
            {"apply_updates": "apply_updates", END: END},
        )
        builder.add_edge("apply_updates", END)

    def build_graph(self):
        builder = StateGraph(GraphState)
        self._add_report_branch(builder)
        self._add_qa_branch(builder)

        # Cả ba chỗ rẽ nhánh dùng chung một API, chỉ khác điểm xuất phát:
        # START / ask_confirm / agent. set_conditional_entry_point() làm đúng
        # việc này nhưng là API riêng cho mỗi START, đọc thành ngoại lệ.
        builder.add_conditional_edges(
            START, self.route_entry, {"report": "extract_tasks", "qa": "agent"}
        )

        return self._with_tracing(builder.compile(checkpointer=self.checkpointer))
