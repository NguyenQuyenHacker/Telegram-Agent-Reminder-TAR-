"""Node `agent`: schema và hàm thuần cho hai chế độ quyết định / chọn lọc.

Chế độ do `messages` quyết: chưa có ToolMessage thì QUYẾT ĐỊNH có tra hay không,
đã có thì CHỌN LỌC phần trả lời được câu hỏi. Thân node là `ClientGraph._agent`.

Chọn lọc trả về CHỈ SỐ, không trả về chữ — code cắt trên payload gốc nên nội
dung đi qua nguyên vẹn, model không có cơ hội sửa một con số. Cùng khuôn với
`subgraph.nodes.keep_only`.
"""

import json
import logging
from typing import Any

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field

from TAR_agent.graph_client.nodes.compose import (
    NO_PASSAGE,
    UNSUPPORTED_HEAD,
    tool_payload,
    unsupported_note,
)

log = logging.getLogger(__name__)


class RetrievalDecision(BaseModel):
    """Chế độ 1: lượt này có cần tra kho không."""

    needs_retrieval: bool = Field(
        default=True,
        description=(
            "true khi câu hỏi cần dữ liệu trong kho dự án. false khi trả lời "
            "được bằng chính hội thoại phía trên (hỏi lại về câu vừa trả lời, "
            "cảm ơn, chào hỏi, hỏi cách dùng bot)."
        ),
    )
    query: str = Field(
        default="",
        description=(
            "Câu đem đi tra khi needs_retrieval là true. Giữ NGUYÊN VĂN mã hiệu, "
            "tên công việc, tên đơn vị trong câu người dùng gõ."
        ),
    )
    direct_answer: str = Field(
        default="",
        description=(
            "Câu trả lời tiếng Việt khi needs_retrieval là false. "
            "Rỗng khi needs_retrieval là true."
        ),
    )


class EvidenceSelection(BaseModel):
    """Chế độ 2: giữ lại đúng phần trả lời được câu hỏi."""

    keep_rows: list[int] = Field(
        default_factory=list,
        description="Số thứ tự các dòng bảng (D1, D2, ...) cần giữ. Chỉ điền số, không kèm chữ D.",
    )
    keep_passages: list[int] = Field(
        default_factory=list,
        description="Số thứ tự các đoạn tài liệu (P1, P2, ...) cần giữ.",
    )
    outline: str = Field(
        default="",
        description=(
            "Một hai câu dặn người soạn: câu hỏi thật sự đang hỏi gì, và phần "
            "giữ lại trả lời nó ra sao. Không viết sẵn câu trả lời."
        ),
    )


def number_evidence(tool_messages: list[Any]) -> str:
    """Mọi thứ tra được -> một danh mục ĐÁNH SỐ để model chỉ mặt từng mục.

    Số chạy suốt cả lượt (không đánh lại ở mỗi ToolMessage) vì vòng revise tra
    thêm một lô, và hai lô trùng số thì "giữ D3" không chỉ vào đâu cả. Đếm từ 1
    vì model luôn đếm từ 1; `filter_evidence` trừ lại.

    Khối `[KHÔNG TRA ĐƯỢC · ...]` không đánh số — nó là lý do vì sao không có dữ
    liệu, không phải dữ liệu để chọn, và luôn đi tiếp tới compose.
    """
    blocks: list[str] = []
    row_no = passage_no = 0

    for message in tool_messages:
        payload = tool_payload(message)
        if payload is None:
            continue

        if reason := unsupported_note(payload):
            blocks.append(f"[{UNSUPPORTED_HEAD}]\n{reason}")

        rows = payload.get("rows") or []
        if rows:
            columns = payload.get("columns") or list(rows[0].keys())
            lines = [
                f"[BẢNG LỊCH CÔNG VIỆC · SQL: {payload.get('sql', '')}]",
                "cột: " + " | ".join(str(c) for c in columns),
            ]
            for row in rows:
                row_no += 1
                values = " | ".join(
                    "" if row.get(c) is None else str(row.get(c)) for c in columns
                )
                lines.append(f"D{row_no}. {values}")
            blocks.append("\n".join(lines))

        for passage in payload.get("passages") or []:
            passage_no += 1
            head = (
                f"P{passage_no}. {passage.get('file_name', '')} · "
                f"{passage.get('as_of_date', '')}"
            )
            if passage.get("heading_path"):
                head += f" · {passage['heading_path']}"
            blocks.append(f"{head}\n{passage.get('content', '')}")

    return "\n\n".join(blocks) if blocks else NO_PASSAGE


def count_evidence(tool_messages: list[Any]) -> tuple[int, int]:
    """(số dòng bảng, số đoạn tài liệu) tra được trong lượt.

    Nơi gọi cần con số này để biết một lựa chọn RỖNG nghĩa là "không có gì để
    chọn" hay "model vừa vứt sạch dữ liệu đang có".
    """
    rows = passages = 0
    for message in tool_messages:
        payload = tool_payload(message)
        if payload is None:
            continue
        rows += len(payload.get("rows") or [])
        passages += len(payload.get("passages") or [])
    return rows, passages


def describe_rows(tool_messages: list[Any]) -> str:
    """Câu SQL đã sinh + nguyên văn từng dòng nó trả về, đánh số như `number_evidence`.

    Chỉ để LOG, không đi vào prompt nào. Nơi gọi in nó cạnh chỉ số `select` giữ
    lại: "giữ 0/9 dòng" một mình không nói được là `select` bỏ nhầm hay `gen_sql`
    khớp nhầm nhóm, mà hai chuyện đó sửa ở hai file khác nhau.
    """
    lines: list[str] = []
    row_no = 0

    for message in tool_messages:
        payload = tool_payload(message)
        if payload is None:
            continue
        rows = payload.get("rows") or []
        if not rows:
            continue
        columns = payload.get("columns") or list(rows[0].keys())
        lines.append(f"  SQL: {payload.get('sql', '')}")
        for row in rows:
            row_no += 1
            values = " | ".join(
                "" if row.get(c) is None else str(row.get(c)) for c in columns
            )
            lines.append(f"  D{row_no}. {values}")

    return "\n".join(lines) or "  (nhánh bảng không trả dòng nào)"


def filter_evidence(
    tool_messages: list[Any], keep_rows: list[int], keep_passages: list[int]
) -> list[Any]:
    """Cắt theo chỉ số 1-based, trả ToolMessage mới cùng khuôn payload cũ.

    Cùng khuôn để `render_tool_results` dùng lại được nguyên vẹn. Chỉ số ngoài
    khoảng thì bỏ qua chứ không ném — model bịa ra `D40` cho một lô 27 dòng là
    chuyện có thật.

    Message bị lọc sạch và không mang lời từ chối thì bỏ hẳn; payload không đọc
    được thì giữ nguyên (nó không được đánh số nên model không chọn được, mà vứt
    đi thì mất một kết quả tra).
    """
    wanted_rows = set(keep_rows)
    wanted_passages = set(keep_passages)
    kept: list[Any] = []
    row_no = passage_no = 0

    for message in tool_messages:
        payload = tool_payload(message)
        if payload is None:
            kept.append(message)
            continue

        rows: list[Any] = []
        for row in payload.get("rows") or []:
            row_no += 1
            if row_no in wanted_rows:
                rows.append(row)

        passages: list[Any] = []
        for passage in payload.get("passages") or []:
            passage_no += 1
            if passage_no in wanted_passages:
                passages.append(passage)

        has_refusal = bool(unsupported_note(payload))
        if not rows and not passages and not has_refusal:
            continue

        filtered = {k: v for k, v in payload.items() if k not in ("rows", "passages")}
        if rows:
            filtered["rows"] = rows
        if passages:
            filtered["passages"] = passages

        kept.append(
            ToolMessage(
                content=json.dumps(filtered, ensure_ascii=False),
                tool_call_id=getattr(message, "tool_call_id", "retrieve_all"),
                name="retrieve_all",
            )
        )

    return kept
