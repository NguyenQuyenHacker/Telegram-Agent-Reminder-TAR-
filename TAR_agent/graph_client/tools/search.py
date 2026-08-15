"""Tool tra cứu của agent client. CHỈ ĐỌC.

Vỏ @tool mỏng bọc `graph_client/subgraph` — docstring của tool là thứ LLM đọc để
quyết định gọi gì với tham số gì, nên nó viết cho model chứ không phải cho người
bảo trì. Chú thích cho người bảo trì thì nằm ở đây, ngoài docstring.

`project_id` KHÔNG phải tham số model sinh ra: nó đi vào bằng `InjectedState`,
nên không nằm trong schema mà LLM nhìn thấy và LLM không có đường nào tra sang
dự án khác. Node `identify_project` là chỗ duy nhất quyết định giá trị đó, và
graph bảo đảm nó khác None trước khi agent chạy.

Tool CHỈ trả nội dung và nguồn — không trả `distance`, không trả `score`. Đưa
điểm số cho model là mời nó viết "độ liên quan 0.87" vào câu trả lời, một con số
người đọc không diễn giải được và cũng không có nghĩa gì ngoài lần tra đó.
"""

import logging
import uuid
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from TAR_agent.graph_client.subgraph.graph import SEARCH_GRAPH

log = logging.getLogger(__name__)


@tool
async def search_docs(
    query: str,
    project_id: Annotated[uuid.UUID, InjectedState("project_id")],
) -> dict:
    """Tìm đoạn tài liệu liên quan trong kho của dự án đang hỏi.

    Luôn trả về phần LIÊN QUAN NHẤT, KHÔNG phải toàn bộ. Câu hỏi cần danh sách
    đầy đủ hoặc một con số ("bao nhiêu", "tổng", "liệt kê tất cả") thì dùng
    query_data — đếm trên kết quả của tool này luôn ra thiếu.

    Tool TỰ thử lại với từ khoá khác nếu lần đầu không ra gì. Không cần gọi
    lại tool này với cách diễn đạt khác — đã làm rồi. Trả status "empty"
    nghĩa là kho thật sự không có, hãy nói thẳng với người dùng.

    Args:
        query: nội dung cần tìm, viết thành câu đầy đủ ý.
    """
    result = await SEARCH_GRAPH.ainvoke(
        {
            "question": query,
            "original": query,
            "project_id": project_id,
            "attempt": 0,
            "docs": [],
            "ok": False,
        }
    )

    docs = result.get("docs") or []
    log.info(
        "search_docs(%r) -> %d đoạn sau %d lượt tra",
        query,
        len(docs),
        result.get("attempt", 0),
    )
    if not docs:
        return {"status": "empty"}

    return {
        "status": "ok",
        # Sự thật LÚC CHẠY, và đây là thứ dạy model chọn tool chứ không phải
        # prompt: retriever lấy `k` đoạn liên quan nhất, KHÔNG BAO GIỜ lấy hết.
        # Không nói ra thì model đếm "4 việc" từ 5 đoạn và tin là đủ — mà
        # `grounding.check` không cứu được ca đó, vì số 4 có thật trong tài
        # liệu. Đúng ranh giới grounding tự ghi: chặn bịa SỐ, không chặn bịa Ý.
        "coverage": "partial",
        "passages": [
            {
                "content": doc.content,
                "file_name": doc.file_name,
                "as_of_date": doc.as_of_date.isoformat(),
                "heading_path": doc.heading_path,
            }
            for doc in docs
        ],
    }
