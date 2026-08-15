"""Tool của agent client — CHỈ ĐỌC, không ngoại lệ.

Danh sách này tách hẳn khỏi ADMIN_TOOLS và không import từ graph_admin. Gộp một
danh sách rồi lọc lúc chạy là sớm muộn cũng lọt.

HAI tool, hai giới hạn ngược nhau, và ranh giới giữa chúng nằm ở DOCSTRING chứ
không ở system prompt:

    search_docs   văn xuôi — "vì sao", "quy định thế nào". Trả phần LIÊN QUAN
                  NHẤT, không bao giờ trả hết, và tự khai điều đó bằng
                  `coverage: "partial"` trong payload.
    query_data    số liệu — "bao nhiêu", "tổng", "liệt kê tất cả". Chạy SQL
                  trên toàn bộ bảng nên con số nó trả về là con số đầy đủ.

KHÔNG có node router chọn tool đứng trước `agent`. Đó sẽ là lượt LLM thứ ba mỗi
lượt hỏi, và nó sẽ mâu thuẫn với chính phán đoán của `agent` — router bảo SQL,
agent vẫn gọi RAG, giờ tin ai? Chọn tool là việc tool-calling sinh ra để làm;
cần đúng hai thứ là docstring tốt và `max_tool_rounds` đủ rộng để gọi được cả
hai.

`list_projects` biến mất vì việc chọn dự án đã lên node `identify_project` — để
agent tự chọn là để nó tra nhầm dự án, mà câu trả lời sai dự án trông y hệt câu
trả lời đúng.
"""

from TAR_agent.graph_client.tools.query import query_data
from TAR_agent.graph_client.tools.search import search_docs

CLIENT_TOOLS = [search_docs, query_data]

__all__ = ["CLIENT_TOOLS", "query_data", "search_docs"]
