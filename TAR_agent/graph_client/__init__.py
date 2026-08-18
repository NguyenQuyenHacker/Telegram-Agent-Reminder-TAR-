"""Graph của bot client: hỏi đáp trên tài liệu đã nạp.

    START → reset → identify_project → agent ⇄ retrieve → compose → respond → END
                          │              │                   │
                          │              └── không cần tra ──┴─→ respond → END
                          └──────→ respond → END

`retrieve` tra CẢ HAI nguồn cùng lúc — bảng lịch công việc (SQL) và kho tài liệu
(vector + BM25). Không có bước nào CHỌN nguồn; chọn sai là hỏng theo kiểu tệ
nhất, im lặng trả rỗng trong khi nguồn kia đang có sẵn câu trả lời.

`agent` là node hai chế độ, đứng ở cả hai đầu của `retrieve`: trước khi tra nó
quyết định lượt này có cần tra không, sau khi tra nó đọc câu hỏi cạnh dữ liệu và
giữ lại đúng phần trả lời được. Chi tiết và bốn cái trần chặn vòng lặp: graph.py.

CHỈ ĐỌC. Không import nào trong package này chạm được tới hàm ghi kho —
`graph_admin` không được import từ đây.

Ba chỗ dễ sai:

1. Câu trả lời phải KÈM NGUỒN (tên file + mốc dữ liệu), cưỡng chế bằng máy ở
   helpers/grounding.py chứ không chỉ bằng prompt.
2. Tra không ra gì thì nói thẳng kho không có, cấm suy diễn từ kiến thức chung.
3. `project_id` do graph quyết, không phải LLM — trả lời đúng câu hỏi nhưng sai
   dự án trông y hệt trả lời đúng.
"""
