import uuid
from datetime import date

from sqlmodel import Field, SQLModel


class CongViec(SQLModel, table=True):
    """Một dòng lịch công việc đã trích từ tài liệu.

    Nằm ở schema `data` chứ không phải `public`, và đó là toàn bộ điểm của bảng
    này: `data` là thứ duy nhất role `tar_ro` được `GRANT`, nên SQL do LLM sinh
    ra không với tới `project`, `source_document` hay `doc_chunk`.

    Cùng DATABASE với ba bảng kia, chỉ khác schema — Postgres không truy vấn
    chéo database, tách hẳn ra là mất khoá ngoại, mất CASCADE, và mất khả năng
    ghi cùng MỘT transaction với chunk. Cái muốn tách là QUYỀN, và GRANT làm
    được việc đó mà không đụng tới liên kết.

    `project_id` và `as_of_date` lặp lại từ source_document vì cùng lý do như
    `doc_chunk`: policy RLS lọc thẳng ở cột này chứ không qua một JOIN mà ai đó
    có thể quên — và JOIN đó `tar_ro` cũng không có quyền viết.

    KHÔNG có cột `du_an`: nó trùng `project.name` đã suy được qua `project_id`.
    Tên dự án lấy ở tầng app từ state hội thoại, không qua SQL của LLM.
    """

    __tablename__ = "cong_viec"
    __table_args__ = {"schema": "data"}

    row_id: uuid.UUID = Field(primary_key=True)
    # `source_document.document_id`, KHÔNG phải `public.source_document...`:
    # SQLAlchemy phân giải khoá ngoại theo KHOÁ TRONG METADATA, mà `SourceDocument`
    # không khai schema nên khoá của nó là `source_document` trơn. Viết kèm
    # `public.` thì nó đi tìm một bảng tên "public.source_document" không tồn tại
    # và ném NoReferencedTableError — ném lúc CẤU HÌNH MAPPER, tức là ở lời gọi
    # ORM đầu tiên chạm tới lớp này chứ không phải lúc import. Nghĩa là mọi thứ
    # trông vẫn chạy cho tới đúng lúc `writer.save` ghi dòng đầu tiên.
    # DDL sinh ra vẫn đúng: `REFERENCES public.source_document`, vì bảng đích
    # nằm ở schema mặc định.
    document_id: uuid.UUID = Field(
        foreign_key="source_document.document_id", index=True
    )
    project_id: uuid.UUID = Field(index=True)
    as_of_date: date

    # Đoạn đã sinh ra dòng này. NULL khi không truy được — thà để trống còn hơn
    # gán bừa một chunk, vì đây chính là thứ dùng để truy ngược số về nguồn.
    chunk_id: uuid.UUID | None = None

    giai_doan: str | None = None
    nhom: str | None = None
    nhom_con: str | None = None
    cong_viec: str
    don_vi: str | None = None
    ngay_bd: date | None = None
    ngay_ht: date | None = None

    # TÍNH bằng Python từ hai mốc (xem graph_admin/helpers/rowcheck.py), không
    # nhận từ LLM.
    so_ngay: int | None = None

    # Ba cột văn xuôi tự do. Không enum, không CHECK, không chuẩn hoá — ép
    # chúng vào một tập giá trị cố định là bịa ra thông tin không có trong file,
    # ở tầng không ai kiểm lại được.
    can_cu_phap_ly: str | None = None
    ket_qua_dau_ra: str | None = None
    ghi_chu: str | None = None
