"""Đọc file thành Document rồi cắt chunk. KHÔNG ghi gì vào DB.

Loader và splitter đều ĐỒNG BỘ và thuần CPU (mở workbook, đọc file text) — bọc
asyncio.to_thread. Chạy thẳng trên event loop là treo cả hai bot trong lúc đọc
một file lớn.

Node này chạy SAU cả hai điểm dừng, nên tới đây là chắc chắn admin đã đồng ý
nạp. Không bao giờ tốn công đọc một file sắp bị huỷ.
"""

import asyncio
import logging

from TAR_agent.graph_admin.state import AdminState
from TAR_agent.graph_admin.helpers import loaders
from TAR_agent.graph_admin.helpers.filename import guess_as_of
from TAR_agent.graph_admin.helpers.split import split_documents
from TAR_agent.utils.config import now_local

log = logging.getLogger(__name__)


def _load_and_split(path, file_name):
    documents = loaders.load(path, file_name=file_name)
    return documents, split_documents(documents)


async def parse(state: AdminState) -> dict:
    upload = state["upload"]
    assert upload is not None

    if not upload.path.exists():
        return {"error": "temp_file_gone"}

    try:
        documents, chunks = await asyncio.to_thread(
            _load_and_split, upload.path, upload.file_name
        )
    except loaders.UnsupportedFormat:
        # download.py đã chặn từ trước; tới được đây là ai đó gọi graph trực tiếp
        return {"error": "unsupported_format"}
    except Exception:
        log.exception("Đọc hỏng: %s", upload.file_name)
        return {"error": "parse_failed"}

    if not chunks:
        # File .txt rỗng, sheet .xlsx trắng. Loader trả về Document rỗng chứ
        # không báo lỗi — phải tự bắt ở đây.
        log.warning("Không rút được nội dung nào từ %s", upload.file_name)
        return {"error": "empty_document"}

    # Không đoán được mốc thì lấy hôm nay. report luôn in mốc ra để admin soát và
    # nạp lại với tên file rõ ràng hơn nếu sai.
    as_of = guess_as_of(upload.file_name) or now_local().date()

    log.info(
        "%s -> %d Document -> %d chunk, mốc %s",
        upload.file_name, len(documents), len(chunks), as_of,
    )
    return {
        "file_kind": loaders.file_kind(state["base_name"]),
        "documents": documents,
        "chunks": chunks,
        "as_of_date": as_of,
    }


def choose_branch(state: AdminState) -> str:
    return "report" if state.get("error") else "store"
