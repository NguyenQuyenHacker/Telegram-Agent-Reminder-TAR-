"""Graph của bot client: hỏi đáp trên tài liệu đã nạp.

    START → reset → identify_project → agent ⇄ tools → compose → respond → END
                          │                               ↑          │
                          └──────→ respond → END          └── "thieu" ┘

Chi tiết từng nhánh và bốn cái trần chặn vòng lặp: xem graph.py.

CHỈ ĐỌC. Không node nào, không tool nào, không import nào trong package này chạm
được tới hàm ghi kho — `graph_admin` không được import từ đây, và đó là thứ giữ
cho ranh giới quyền là ranh giới thật chứ không phải quy ước.

Ba chỗ dễ sai, ghi trước để khỏi phải học lại:

1. Câu trả lời phải KÈM NGUỒN — tên file và mốc dữ liệu. Không có nguồn thì
   người đọc không kiểm chứng được, và một câu bịa trông y hệt một câu đúng.
   Cưỡng chế bằng máy ở helpers/grounding.py, không chỉ bằng prompt.

2. Tra không ra gì thì nói thẳng là kho không có. Cấm suy diễn từ kiến thức
   chung của model — người dùng đang hỏi về dự án của họ, không phải hỏi
   Wikipedia.

3. `project_id` do graph quyết, không phải LLM. Trả lời đúng câu hỏi nhưng sai
   dự án trông y hệt trả lời đúng, nên mỗi câu trả lời phải tự nêu tên dự án nó
   đang nói tới — xem prompts/client_system/compose.md.
"""
