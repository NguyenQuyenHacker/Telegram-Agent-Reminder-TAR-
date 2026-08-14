"""Chunk tra được -> câu trả lời có trích nguồn.

Chỗ duy nhất trong luồng client gọi LLM.

Hai điều kiện bắt buộc, đặt trong prompt lẫn kiểm ở đây:
  - Mỗi ý dẫn ra phải kèm tên file + mốc dữ liệu (as_of_date).
  - `retrieved` rỗng -> trả lời "kho không có", KHÔNG gọi LLM. Đưa danh sách
    rỗng cho model rồi bảo nó trả lời là mời nó bịa.
"""

from TAR_agent.graph_client.state import ClientState


async def generate(state: ClientState) -> dict:
    raise NotImplementedError
