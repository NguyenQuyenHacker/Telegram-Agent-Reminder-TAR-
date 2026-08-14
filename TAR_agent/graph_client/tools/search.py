"""Tool tra cứu của agent client. CHỈ ĐỌC.

Vỏ @tool mỏng bọc TAR_agent/graph_client/helpers/retriever.py — docstring ở đây là thứ LLM
đọc để quyết định gọi hàm nào với tham số gì, nên viết cho model hiểu chứ không
phải cho người bảo trì.

`search_documents` bắt buộc có `project`: phân giải không ra thì trả lỗi kèm lời
nhắc gọi `list_projects`, tuyệt đối không tự chọn một dự án nào đó rồi tra bừa.
"""

from langchain_core.tools import tool


@tool
async def list_projects() -> list[dict]:
    """Liệt kê các dự án đang có trong kho, kèm số tài liệu và mốc dữ liệu mới nhất.

    Gọi tool này trước khi tra cứu nếu chưa chắc tên dự án người dùng nói tới.
    """
    raise NotImplementedError


@tool
async def search_documents(project: str, query: str, k: int = 5) -> list[dict]:
    """Tìm đoạn tài liệu liên quan trong phạm vi MỘT dự án.

    Args:
        project: tên dự án. Bắt buộc — không có thì gọi list_projects trước.
        query: nội dung cần tìm, viết lại thành câu đầy đủ ý.
        k: số đoạn lấy về, mặc định 5.
    """
    raise NotImplementedError
