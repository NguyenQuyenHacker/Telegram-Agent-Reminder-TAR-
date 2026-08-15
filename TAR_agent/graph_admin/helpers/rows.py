"""Hình dạng một dòng lịch công việc, ở ba giai đoạn của đường đi.

    CongViecRow   thứ LLM điền           (không có so_ngay, không có du_an)
    RawRow        + ngữ cảnh của file    (so_ngay ghi sẵn, chunk_id sinh ra nó)
    CheckedRow    sau `rowcheck.check`   (so_ngay đã TÍNH, sẵn sàng ghi DB)

Tách ba lớp chứ không dùng một: cái LLM được phép điền và cái ghi xuống DB là
hai tập trường khác nhau, và chỗ chúng khác nhau chính là chỗ dễ sai nhất.

`so_ngay` KHÔNG có trong `CongViecRow` — nó suy được từ hai mốc, để LLM điền là
mời nó tự tính rồi tính sai. `rowcheck` tính bằng Python.

`du_an` cũng KHÔNG có: nó trùng `project.name`, mà dự án được xác định TRƯỚC khi
đọc file (node `ask_project`). Cho LLM đọc lại một thứ đã biết chắc là tạo ra
một bản sao có thể lệch chữ so với bản gốc.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field


class CongViecRow(BaseModel):
    """Một dòng công việc do LLM rút ra. Mọi trường trừ `cong_viec` được để trống.

    Ba trường tự do cuối (`can_cu_phap_ly`, `ket_qua_dau_ra`, `ghi_chu`) CHÉP
    NGUYÊN VĂN: không tóm tắt, không suy ra trạng thái, không dịch sang enum.
    Ràng buộc đó chỉ đặt được ở prompt — `rowcheck` không có tập giá trị hợp lệ
    nào để đối chiếu văn xuôi tự do.
    """

    giai_doan: str | None = Field(default=None, description="Giai đoạn (mã cấp I).")
    nhom: str | None = Field(default=None, description="Nhóm việc (mã cấp I.1).")
    nhom_con: str | None = Field(default=None, description="Nhóm con (mã cấp I.2.1).")
    cong_viec: str = Field(description="Tên hạng mục thực hiện. BẮT BUỘC.")
    don_vi: str | None = Field(default=None, description="Đơn vị thực hiện.")
    ngay_bd: date | None = Field(default=None, description="Ngày bắt đầu.")
    ngay_ht: date | None = Field(default=None, description="Ngày hoàn thành.")
    can_cu_phap_ly: str | None = Field(default=None, description="Chép nguyên văn.")
    ket_qua_dau_ra: str | None = Field(default=None, description="Chép nguyên văn.")
    ghi_chu: str | None = Field(default=None, description="Chép nguyên văn.")


class RowBatch(BaseModel):
    """`output_schema` của lượt rút dòng từ `.txt`. Lô chunk -> danh sách dòng."""

    rows: list[CongViecRow] = Field(
        default_factory=list, description="Mọi dòng công việc tìm được trong lô này."
    )


class HeaderMap(BaseModel):
    """`output_schema` của lượt ánh xạ header `.xlsx`: trường -> TÊN CỘT trong file.

    Một trường một thuộc tính chứ không phải `dict[str, str]`: model không bịa
    được tên trường lạ, và thiếu một cột thì đó là `None` chứ không phải một
    khoá viết sai chính tả mà không ai bắt.

    KHÔNG có `giai_doan` / `nhom` / `nhom_con` — ba trường đó suy ra bằng Python
    từ mã ở cột TT (xem `extract.split_rows`), không phải thứ đọc từ tên cột.
    """

    tt: str | None = Field(
        default=None,
        description='Cột số thứ tự / mã mục ("TT", "STT"). Dùng để phân loại dòng.',
    )
    cong_viec: str | None = Field(
        default=None, description='Cột tên việc, thường là "Hạng mục thực hiện".'
    )
    don_vi: str | None = Field(default=None, description="Cột đơn vị thực hiện.")
    ngay_bd: str | None = Field(default=None, description="Cột ngày bắt đầu.")
    ngay_ht: str | None = Field(default=None, description="Cột ngày hoàn thành.")
    so_ngay: str | None = Field(default=None, description="Cột số ngày / thời gian.")
    can_cu_phap_ly: str | None = Field(default=None, description="Cột căn cứ pháp lý.")
    ket_qua_dau_ra: str | None = Field(default=None, description="Cột kết quả đầu ra.")
    ghi_chu: str | None = Field(default=None, description="Cột ghi chú.")


@dataclass(frozen=True)
class RawRow:
    """Một dòng trước khi qua `rowcheck`, kèm hai thứ LLM không được đụng vào.

    `so_ngay_in_file` là con số FILE ghi sẵn, giữ lại CHỈ để đối chiếu với hiệu
    hai mốc. Lệch thì báo admin chứ không âm thầm chọn một bên — lệch đó thường
    là dấu hiệu file có lỗi, không phải dấu hiệu cần sửa dữ liệu.
    """

    row: CongViecRow
    so_ngay_in_file: int | None = None
    chunk_id: uuid.UUID | None = None


@dataclass(frozen=True)
class CheckedRow:
    """Đúng hình dạng ghi xuống `data.cong_viec`. `so_ngay` đã tính, không nhận."""

    giai_doan: str | None
    nhom: str | None
    nhom_con: str | None
    cong_viec: str
    don_vi: str | None
    ngay_bd: date | None
    ngay_ht: date | None
    so_ngay: int | None
    can_cu_phap_ly: str | None
    ket_qua_dau_ra: str | None
    ghi_chu: str | None
    chunk_id: uuid.UUID | None

    def dedupe_key(self) -> tuple:
        """Khoá khử trùng cho nhánh `.txt`: chunk có chồng lấn nên cùng một dòng
        xuất hiện ở hai lô liền nhau là chuyện thường."""
        return (self.giai_doan, self.nhom, self.cong_viec, self.ngay_bd)
