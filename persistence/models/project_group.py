import uuid

from sqlmodel import Field, SQLModel


class ProjectGroup(SQLModel, table=True):
    """Một nhóm dự án: danh tính, tên hiển thị, và bộ đếm mã việc.

    Không đặt tên bảng là "group" vì đó là từ khoá SQL, phải quote ở mọi query.
    """

    __tablename__ = "project_group"

    # uuid5(namespace, normalized_name) — xem app/core/task_code.group_uuid.
    # Suy được từ tên nên không cần tra DB mới biết một nhóm mang id nào.
    group_id: uuid.UUID = Field(primary_key=True)
    # Tên của lần gặp gần nhất, dùng để hiển thị. normalized_name mới là thứ
    # sinh ra group_id, giữ lại để tra ngược và để soát khi hai biến thể gộp lại.
    name: str
    normalized_name: str = Field(unique=True)
    # "App Trưởng thôn, trưởng bản" -> TB-001, TB-002, ...
    prefix: str = Field(unique=True)
    # Số THỨ TỰ KẾ TIẾP sẽ cấp, không phải số đã cấp. Chỉ tăng, không bao giờ
    # lùi: việc xong hay bị hủy vẫn giữ mã của nó, cấp lại là "TB-002" trỏ vào
    # hai việc khác nhau.
    next_seq: int = 1
