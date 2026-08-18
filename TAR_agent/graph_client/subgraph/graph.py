"""Nối dây vòng tra cứu tự sửa truy vấn.

    START → retrieve → grade ─ đạt / hết lượt ──→ END
                         └─ chưa đạt ──→ rewrite → retrieve

Dựng bằng CLASS chứ không bằng hàm module-level: hai node ở đây gọi hai model
khác nhau, và model phải dựng một lần lúc khởi tạo. Viết bằng hàm thì hoặc dựng
lại model mỗi lời gọi (một lượt HTTP thừa để đọc lại cấu hình đã có), hoặc phải
có biến toàn cục — mà biến toàn cục thì không đặt tên nói rõ nó là model của
node nào.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from TAR_agent.graph_client.helpers import retriever
from TAR_agent.graph_client.subgraph.nodes import Grade, keep_only, number_passages
from TAR_agent.graph_client.subgraph.state import SearchState
from TAR_agent.utils.config import create_google_genai, load_config, load_prompt
from TAR_agent.utils.timing import timed_node

log = logging.getLogger(__name__)


class SearchGraph:
    """Vòng tra cứu tự sửa truy vấn. Compile một lần, không checkpointer."""

    def __init__(self) -> None:
        config = load_config()
        models = config["models"]
        self.retrieval = config["retrieval"]
        self.max_attempts = self.retrieval["max_attempts"]

        self.doc_grader = create_google_genai(models["grade"], output_schema=Grade)
        self.query_rewriter = create_google_genai(models["rewrite"])

    async def _retrieve(self, state: SearchState) -> dict:
        question = state["question"]
        docs = await retriever.search(
            state["project_id"], question, self.retrieval["k"]
        )
        attempt = state.get("attempt", 0) + 1
        log.info("Tra lượt %d: %r -> %d đoạn", attempt, question, len(docs))
        return {"docs": docs, "attempt": attempt}

    async def _grade(self, state: SearchState) -> dict:
        """MỘT lượt LLM chấm cả lô, chấm theo `original` chứ không theo `question`.

        Hỏng ở đây thì MỞ chứ không đóng: giữ nguyên lô đoạn và coi như đạt. Đóng
        lại (coi như trượt) thì một lỗi mạng thoáng qua biến thành câu "kho không
        có tài liệu về việc này" — người dùng tin, và không có gì báo là sai.
        """
        docs = state.get("docs") or []
        if not docs:
            return {"ok": False}

        payload = (
            f"CÂU HỎI:\n{state['original']}\n\n"
            f"CÁC ĐOẠN TRA ĐƯỢC:\n{number_passages(docs)}"
        )
        try:
            grade: Grade = await self.doc_grader.ainvoke(
                {
                    "system": [SystemMessage(load_prompt("client_system/grade_docs"))],
                    "messages": [HumanMessage(payload)],
                }
            )
        except Exception:
            log.exception("Chấm đoạn thất bại — giữ nguyên lô và đi tiếp")
            return {"ok": True}

        kept = keep_only(docs, grade.keep)
        ok = bool(grade.enough and kept)
        log.info("Chấm: giữ %d/%d, đủ=%s — %s", len(kept), len(docs), ok, grade.reason)

        # Hết lượt mà vẫn chưa đạt -> trả RỖNG, không trả lô nửa vời. Đưa mấy
        # đoạn chỉ chạm chủ đề cho compose là mời nó nối các mẩu rời thành một
        # câu trả lời nghe hợp lý mà không đoạn nào chứng minh được.
        if not ok and state.get("attempt", 0) >= self.max_attempts:
            return {"ok": False, "docs": []}
        return {"ok": ok, "docs": kept}

    async def _rewrite(self, state: SearchState) -> dict:
        """Đổi từ khoá rồi tra lại. Trả chuỗi thuần, không schema.

        Viết lại từ `original` chứ không từ `question`: nối tiếp bản đã viết lại
        một lần nữa là trôi mỗi vòng một chút cho tới lúc không còn dính gì tới
        câu người dùng hỏi.
        """
        payload = (
            f"CÂU HỎI GỐC:\n{state['original']}\n\n"
            f"TRUY VẤN VỪA THỬ (không ra kết quả dùng được):\n{state['question']}"
        )
        try:
            reply = await self.query_rewriter.ainvoke(
                {
                    "system": [
                        SystemMessage(load_prompt("client_system/rewrite_query"))
                    ],
                    "messages": [HumanMessage(payload)],
                }
            )
            rewritten = str(reply.content).strip()
        except Exception:
            log.exception("Viết lại truy vấn thất bại — giữ nguyên truy vấn cũ")
            rewritten = ""

        # Rỗng thì giữ nguyên: tra lại y hệt tốn một lượt vô ích, còn tra bằng
        # chuỗi rỗng thì BM25 ném và cả lượt hỏi chết.
        if not rewritten:
            return {}
        log.info("Viết lại truy vấn: %r", rewritten)
        return {"question": rewritten}

    def _after_grade(self, state: SearchState) -> str:
        if state.get("ok"):
            return "end"
        # `_grade` đã dọn `docs` về rỗng ở lượt cuối — router không ghi được state.
        if state.get("attempt", 0) >= self.max_attempts:
            return "end"
        return "rewrite"

    def build(self):
        builder = StateGraph(SearchState)
        builder.add_node("retrieve", timed_node("docs.search", self._retrieve))
        builder.add_node("grade", timed_node("docs.grade", self._grade))
        builder.add_node("rewrite", timed_node("docs.rewrite", self._rewrite))

        builder.add_edge(START, "retrieve")
        builder.add_edge("retrieve", "grade")
        builder.add_conditional_edges(
            "grade", self._after_grade, {"rewrite": "rewrite", "end": END}
        )
        builder.add_edge("rewrite", "retrieve")
        # `checkpointer=False` chứ không phải bỏ trống. Bỏ trống thì subgraph
        # THỪA KẾ checkpointer của graph cha qua config lúc chạy, và LangGraph
        # cấm hai subgraph có checkpointer cùng chạy trong MỘT node —
        # `MultipleSubgraphsError`. Mà `retrieve_all` làm đúng điều đó: nó gọi
        # SEARCH_GRAPH và SQL_GRAPH song song trong node `retrieve`.
        #
        # Không mất gì: vòng này là một phép tính bên trong một lời gọi tra cứu,
        # nó không có điểm dừng nào để lưu lại và không ai resume nó.
        return builder.compile(checkpointer=False)


# Dựng một lần lúc import. `tools/retrieval.py` gọi lại nó ở mọi lượt tra.
SEARCH_GRAPH = SearchGraph().build()
