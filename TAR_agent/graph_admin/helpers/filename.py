"""Rút danh tính và mốc dữ liệu từ tên file. Hàm thuần, không I/O.

    base_name("TiendoT6(1).xlsx")  -> "TiendoT6.xlsx"
    guess_as_of("TiendoT6.xlsx")   -> 2026-06-30
"""

import re
from datetime import date, timedelta
from pathlib import PurePosixPath

# Hậu tố " (1)" Telegram/Windows thêm khi tải trùng tên. CHỈ bắt số —
# "Bao cao (ban chinh).pdf" là tên người đặt.
_COPY_SUFFIX = re.compile(r"\s*\(\d+\)$")

# Chèn "_" ở ranh giới camelCase: "TiendoT6" không có dấu phân cách nào trước
# "T6" nên các mẫu dưới đều trượt nếu không tách.
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")

# Thứ tự QUAN TRỌNG: cụ thể trước, mơ hồ sau. "2026-06-30" cũng khớp mẫu
# tháng-không-năm (bắt "30" thành tháng) nếu để mẫu lỏng chạy trước.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})[-_./](\d{1,2})[-_./](20\d{2})(?!\d)"), "dmy"),
    (re.compile(r"(?:^|[^a-z0-9])t(?:hang)?\s*_?(\d{1,2})[-_. ]+(20\d{2})", re.I), "my"),
    (re.compile(r"(?:^|[^a-z0-9])(?:tuan|w)\s*_?(\d{1,2})(?!\d)", re.I), "week"),
    (re.compile(r"(?:^|[^a-z0-9])t(?:hang)?\s*_?(\d{1,2})(?!\d)", re.I), "month"),
]


def base_name(file_name: str) -> str:
    """Tên chuẩn hoá làm danh tính tài liệu: bỏ đường dẫn, hậu tố "(n)", hạ đuôi."""
    name = PurePosixPath(file_name.replace("\\", "/")).name
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        return " ".join(_COPY_SUFFIX.sub("", name).split())
    return " ".join(f"{_COPY_SUFFIX.sub('', stem)}.{suffix.lower()}".split())


def _end_of_month(year: int, month: int) -> date:
    return date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


def _resolve(kind: str, groups: tuple[str, ...], today: date) -> date | None:
    """Nhóm số bắt được -> ngày. Kỳ báo cáo lấy NGÀY CUỐI kỳ."""
    nums = [int(g) for g in groups]

    if kind in ("ymd", "dmy"):
        y, mo, d = nums if kind == "ymd" else nums[::-1]
        return date(y, mo, d) if 1 <= mo <= 12 and 1 <= d <= 31 else None

    if kind == "my":
        mo, y = nums
        return _end_of_month(y, mo) if 1 <= mo <= 12 else None

    if kind == "week":
        week = nums[0]
        # Chủ nhật của tuần ISO — cuối kỳ, thống nhất với quy ước tháng.
        return date.fromisocalendar(today.year, week, 7) if 1 <= week <= 53 else None

    mo = nums[0]
    if not 1 <= mo <= 12:
        return None
    # Không có năm -> năm nay; tháng ở tương lai gần như chắc là báo cáo năm
    # ngoái nạp muộn.
    return _end_of_month(today.year if mo <= today.month else today.year - 1, mo)


def guess_as_of(file_name: str, today: date | None = None) -> date | None:
    """Mốc DỮ LIỆU đoán từ tên file.

    Không nhận ra thì trả None chứ KHÔNG lấy hôm nay — nơi gọi mới quyết định
    mặc định, và phải in ra cho admin soát.
    """
    today = today or date.today()
    stem = base_name(file_name).rpartition(".")[0] or file_name
    stem = _CAMEL.sub("_", stem)

    for pattern, kind in _PATTERNS:
        if m := pattern.search(stem):
            if found := _resolve(kind, m.groups(), today):
                return found
    return None
