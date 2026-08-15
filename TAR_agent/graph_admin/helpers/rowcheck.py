"""Lọc dòng LLM rút ra trước khi ghi DB. Hàm THUẦN — không I/O, không LLM.

Ba luật cứng, và một phép tính. Không có luật nào cho `ghi_chu` /
`can_cu_phap_ly` / `ket_qua_dau_ra`, và đó là QUYẾT ĐỊNH chứ không phải thiếu
sót: chúng là văn xuôi tự do, không tồn tại tập giá trị hợp lệ để đối chiếu.
Ràng buộc duy nhất là model phải CHÉP chứ không được diễn giải, và ràng buộc đó
chỉ đặt được ở prompt. Bù lại, ba trường đó không bao giờ là lý do loại một
dòng — dòng có số liệu đúng không bị vứt vì một ô chú thích lạ.
"""

from datetime import date

from TAR_agent.graph_admin.helpers.rows import CheckedRow, RawRow
from TAR_agent.utils.text import DATE_RE


def _date_keys(text: str) -> set[tuple[int, int, int | None]]:
    """Mọi ngày viết trong một đoạn văn -> tập khoá `(ngày, tháng, năm|None)`.

    Dùng CHÍNH `DATE_RE` mà `grounding` dùng ở phía trả lời, để hai đầu của hệ
    thống hiểu "một cái ngày" giống hệt nhau.

    Ngày không ghi năm ("30/9") vào tập với `năm = None` và khớp được với mọi
    năm: file thật hay viết tắt như vậy, và đây là lưới bắt LLM bịa chứ không
    phải trình biên dịch — chặt quá thì nó loại đúng những dòng có thật.
    """
    keys: set[tuple[int, int, int | None]] = set()
    for match in DATE_RE.finditer(text):
        parts = [p.strip() for p in match.group().replace("-", "/").split("/")]
        try:
            numbers = [int(p) for p in parts]
        except ValueError:
            continue
        if len(numbers) == 2:
            keys.add((numbers[0], numbers[1], None))
        elif len(numbers) == 3:
            if parts[0] and len(parts[0]) == 4:  # ISO: 2026-09-30
                year, month, day = numbers
            else:  # kiểu Việt: 30/9/2026, 30/9/26
                day, month, year = numbers
                if year < 100:
                    year += 2000
            keys.add((day, month, year))
            keys.add((day, month, None))
    return keys


def _grounded(value: date, keys: set[tuple[int, int, int | None]]) -> bool:
    return (value.day, value.month, value.year) in keys or (
        value.day,
        value.month,
        None,
    ) in keys


def check(rows: list[RawRow], source_text: str) -> tuple[list[CheckedRow], list[str]]:
    """`(dòng đạt, ghi chú cho admin)`. Ba luật, theo thứ tự rẻ trước.

    Danh sách thứ hai là CHÚ THÍCH chứ không phải danh sách dòng bị loại: nó
    còn chứa cảnh báo lệch `so_ngay` của những dòng vẫn được giữ. Số dòng bị
    loại là `len(rows) - len(kept)` — đếm bằng độ dài danh sách này là đếm sai.

    1. `cong_viec` rỗng -> bỏ. Với `.xlsx` luật này hiếm khi kích hoạt vì dòng
       tiêu đề đã bị `split_rows` lọc trước khi tới đây; với `.txt` đây vẫn là
       tuyến phòng thủ chính vì LLM tự do hơn nhiều.
    2. Ngày không có trong `source_text` -> bỏ. Ngày là thứ LLM bịa êm nhất vì
       nó luôn trông hợp lý — "30/09/2026" đọc y như một ngày có thật.
    3. `ngay_ht < ngay_bd` -> bỏ. Không đoán xem model gõ nhầm ô nào.

    `so_ngay` TÍNH ở đây từ hai mốc, cộng 1 để tính cả ngày đầu (file thật đếm
    "01/6 đến 05/6" là 5 ngày, không phải 4). File ghi sẵn mà lệch với hiệu hai
    mốc thì ghi vào lý do cho admin xem — dòng vẫn được GIỮ, vì lệch đó thường
    là dấu hiệu file có lỗi chứ không phải dấu hiệu dòng này sai.
    """
    keys = _date_keys(source_text)
    kept: list[CheckedRow] = []
    rejected: list[str] = []

    for raw in rows:
        row = raw.row
        name = (row.cong_viec or "").strip()
        if not name:
            rejected.append("Dòng không có tên công việc.")
            continue

        ungrounded = [
            value
            for value in (row.ngay_bd, row.ngay_ht)
            if value is not None and not _grounded(value, keys)
        ]
        if ungrounded:
            rejected.append(
                f"{name}: ngày {', '.join(v.isoformat() for v in ungrounded)} "
                f"không có trong tài liệu."
            )
            continue

        if row.ngay_bd and row.ngay_ht and row.ngay_ht < row.ngay_bd:
            rejected.append(
                f"{name}: ngày hoàn thành ({row.ngay_ht.isoformat()}) trước ngày "
                f"bắt đầu ({row.ngay_bd.isoformat()})."
            )
            continue

        so_ngay = None
        if row.ngay_bd and row.ngay_ht:
            so_ngay = (row.ngay_ht - row.ngay_bd).days + 1
            if raw.so_ngay_in_file is not None and raw.so_ngay_in_file != so_ngay:
                rejected.append(
                    f"{name}: file ghi {raw.so_ngay_in_file} ngày nhưng hai mốc "
                    f"cách nhau {so_ngay} ngày — đã dùng {so_ngay}, dòng vẫn giữ."
                )
        elif raw.so_ngay_in_file is not None:
            # Thiếu một mốc thì không tính được, và con số file ghi sẵn là thứ
            # DUY NHẤT có. Nhận nó — nó do người viết file điền, không phải LLM.
            so_ngay = raw.so_ngay_in_file

        kept.append(
            CheckedRow(
                giai_doan=row.giai_doan,
                nhom=row.nhom,
                nhom_con=row.nhom_con,
                cong_viec=name,
                don_vi=row.don_vi,
                ngay_bd=row.ngay_bd,
                ngay_ht=row.ngay_ht,
                so_ngay=so_ngay,
                can_cu_phap_ly=row.can_cu_phap_ly,
                ket_qua_dau_ra=row.ket_qua_dau_ra,
                ghi_chu=row.ghi_chu,
                chunk_id=raw.chunk_id,
            )
        )

    return kept, rejected
