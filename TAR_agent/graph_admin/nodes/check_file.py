"""Dựng danh tính tài liệu rồi tra kho. Điểm dừng thứ hai nằm ở đây.

    chưa có tài liệu cùng tên  -> parse           ("created")
    có, sha256 TRÙNG           -> dừng, không hỏi ("unchanged")
    có, sha256 khác            -> interrupt       ("updated" / "cancelled")

Hash tính từ bytes thô nên nhánh "unchanged" chặn được trước cả parse lẫn embed.

Node có `interrupt()` nên phải SẠCH — chỉ đọc file và DB. Khi resume nó chạy lại
từ dòng 1.
"""

import asyncio
import hashlib
import logging
from pathlib import Path

from langgraph.types import interrupt

from TAR_agent.graph_admin.state import AdminState
from TAR_agent.graph_admin.helpers.filename import base_name
from persistence.proc import documents as doc_proc

log = logging.getLogger(__name__)

# Đọc theo khối thay vì path.read_bytes(): file 20MB nuốt trọn vào RAM là vô cớ,
# nhất là khi có thể có vài lượt nạp chạy song song.
_HASH_CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_HASH_CHUNK):
            digest.update(block)
    return digest.hexdigest()


async def check_file(state: AdminState) -> dict:
    upload = state["upload"]
    project_id = state["project_id"]
    assert upload is not None and project_id is not None

    if not upload.path.exists():
        # App restart trong lúc admin chưa bấm xong -> file tạm bay mất
        log.warning("File tạm không còn: %s", upload.path)
        return {"error": "temp_file_gone"}

    name = base_name(upload.file_name)
    sha = await asyncio.to_thread(_sha256, upload.path)
    found = await asyncio.to_thread(doc_proc.find_by_name, project_id, name)

    base = {"base_name": name, "content_sha256": sha}

    if found is None:
        return {**base, "outcome": "created", "existing_id": None}

    if found.content_sha256 == sha:
        log.info("Bỏ qua %s: nội dung y hệt bản đang có", name)
        return {
            **base,
            "outcome": "unchanged",
            "existing_id": found.document_id,
            "existing_chunk_count": found.chunk_count,
        }

    choice = interrupt(
        {
            "kind": "confirm_overwrite",
            "data": {
                "file_name": name,
                "project_name": state.get("project_name"),
                "existing_chunk_count": found.chunk_count,
                "existing_as_of_date": found.as_of_date.isoformat(),
                "existing_uploaded_at": found.uploaded_at.date().isoformat(),
            },
        }
    )

    if not choice.get("overwrite"):
        log.info("Admin huỷ ghi đè %s", name)
        return {**base, "outcome": "cancelled", "existing_id": found.document_id}

    return {
        **base,
        "outcome": "updated",
        "existing_id": found.document_id,
        "existing_chunk_count": found.chunk_count,
    }


def choose_branch(state: AdminState) -> str:
    """Sau check_file: đi parse, hay dừng luôn ở report."""
    if state.get("error"):
        return "report"
    return "parse" if state.get("outcome") in ("created", "updated") else "report"
