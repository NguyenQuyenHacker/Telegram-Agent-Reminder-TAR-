"""Tầng node của luồng hỏi đáp.

    reset.py             dọn state của lượt trước
    identify_project.py  đang hỏi dự án nào — schema + kiểm tra sau LLM
    compose.py           soạn câu trả lời — schema + dựng khối passage
    respond.py           cửa ra duy nhất — dựng event

Mỗi file ở đây giữ SCHEMA và HÀM THUẦN. Thân node là method của `ClientGraph`
(graph.py), vì mỗi node cần một model riêng đã dựng sẵn lúc khởi tạo graph.

Không node nào ở đây được ghi vào kho. Nếu một ngày phải viết `INSERT` trong thư
mục này thì thứ cần sửa là thiết kế, không phải thư mục.

CỐ Ý không `from .reset import reset` ở đây: xuất lại một HÀM trùng tên với
MODULE thì `from ...nodes import reset` trả về hàm, và `monkeypatch.setattr` lên
nó nổ AttributeError. graph_admin/nodes đang dính đúng bẫy đó — xem chú thích
trong tests/conftest.py. Nơi nào cần thì import thẳng từ module.
"""
