"""Tra cứu của bot client — CHỈ ĐỌC, không ngoại lệ.

MỘT hàm tra cứu duy nhất. Bản trước có hai tool (`search_docs`, `query_data`) và
một model tool-calling đứng chọn giữa chúng; ba thứ hỏng không sửa được bằng
prompt:

  - chọn sai tool thì cả lượt hỏi trả rỗng dù nhánh kia có sẵn câu trả lời
  - việc chọn tốn một lượt LLM mang schema của cả hai tool
  - nó tự viết lại truy vấn, trùng việc với `rewrite` và `repair` ở hai subgraph

`retrieve_all` chạy CẢ HAI nhánh song song và trả một payload có đủ hai bên, nên
không còn `@tool`, không còn `ToolNode`, không còn model bind tool. Node `agent`
của graph vẫn tên `agent` nhưng việc của nó là quyết định có tra không rồi lọc
thứ tra được — không phải chọn nguồn.
"""

from TAR_agent.graph_client.tools.retrieval import retrieve_all

__all__ = ["retrieve_all"]
