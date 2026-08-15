"""Đường dẫn file -> list[Document]. Một cửa vào duy nhất: `load()`.

Hàm ĐỒNG BỘ và nặng: nơi gọi bọc asyncio.to_thread, chạy thẳng trên event loop
là treo cả hai bot.

Phần .xlsx đọc thẳng bằng `openpyxl`, KHÔNG qua `UnstructuredExcelLoader` nữa.
Một lần mở workbook cho ra CẢ HAI đầu ra:

    read_workbook()  -> list[Sheet]      lưới ô còn nguyên kiểu Python, cho `extract`
    load_xlsx()      -> list[Document]   bảng markdown, cho chunk + embed

Đọc bằng `unstructured` rồi trích dữ liệu từ `text_as_html` là round-trip mất
dữ liệu: lưới ô đã bị dẹt, kiểu dữ liệu đã mất, ngày đã thành chữ — rồi phải
parse ngược HTML để dựng lại đúng cái bảng openpyxl vừa đọc xong và vứt đi.
Bỏ hẳn nó cũng gỡ luôn phụ thuộc `pandas` / `libmagic` chưa bao giờ kiểm chứng
được trên Windows.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document
from openpyxl import load_workbook

_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "cp1258")


class UnsupportedFormat(Exception):
    """Đuôi file không nằm trong SUPPORTED."""


@dataclass(frozen=True)
class Sheet:
    """Một sheet đã đọc. `header` là dòng đầu KHÔNG rỗng, `rows` là phần còn lại.

    Ô giữ nguyên kiểu Python (`datetime`, `int`, `None`) chứ không ép về chuỗi:
    đó là toàn bộ lý do bỏ `unstructured`. `extract` đọc ngày từ đây mà không
    phải đoán định dạng, và `so_ngay` tính được bằng phép trừ chứ không bằng
    một regex ngày tháng nữa.
    """

    name: str
    header: list[str]
    rows: list[list[Any]]


def load_txt(path: Path) -> list[Document]:
    """Một file -> một Document. Dò bảng mã tay để khỏi thêm `chardet`."""
    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            return TextLoader(str(path), encoding=encoding).load()
        except (UnicodeDecodeError, RuntimeError) as exc:
            last_error = exc  # TextLoader gói UnicodeDecodeError vào RuntimeError
    raise ValueError(f"Không đọc được bảng mã của {path.name}") from last_error


def cell_text(value: Any) -> str:
    """Một ô -> chuỗi để hiển thị trong bảng markdown.

    `datetime` -> ISO ngày, không kèm "00:00:00": Excel lưu mọi ngày thành
    datetime, in cả phần giờ vào bảng là ba cột toàn số 0 vô nghĩa.

    `float` nguyên (`40.0`) -> `40`: openpyxl trả về float cho mọi ô số, mà
    "40.0 ngày" thì không ai viết như vậy — và grounding sẽ đi tìm chuỗi "40".
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    # Ký tự "|" trong ô sẽ cắt đôi cột của bảng markdown. Xuống dòng cũng vậy.
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def read_workbook(path: Path) -> list[Sheet]:
    """Một sheet -> một `Sheet(name, header, rows)`. Cell giữ nguyên kiểu Python.

    `data_only=True`: lấy giá trị đã tính của công thức, không lấy chuỗi
    "=SUM(...)". `read_only=True`: file vài nghìn dòng không phải nuốt trọn
    vào RAM dưới dạng object openpyxl.

    Dòng rỗng hoàn toàn bị bỏ ngay ở đây — chúng là dòng ngăn cách trong file
    thật, không mang dữ liệu, và để lọt thì `split_rows` phải kiểm lại lần nữa.

    Mở bằng HANDLE chứ không đưa đường dẫn cho openpyxl: đưa đường dẫn thì nó
    kiểm ĐUÔI FILE trước và ném `InvalidFileException`, mà file tạm Telegram
    tải về mang tên ngẫu nhiên KHÔNG CÓ ĐUÔI (xem app/telegram/download.py).
    Đuôi thật đã được `load()` kiểm bằng `file_name` rồi.
    """
    handle = path.open("rb")
    workbook = load_workbook(handle, data_only=True, read_only=True)
    try:
        sheets: list[Sheet] = []
        for worksheet in workbook.worksheets:
            filled = [
                list(row)
                for row in worksheet.iter_rows(values_only=True)
                if any(cell is not None and str(cell).strip() for cell in row)
            ]
            if not filled:
                continue
            # Dòng đầu KHÔNG phải lúc nào cũng là header: file thật hay có một
            # dòng tiêu đề báo cáo ("BÁO CÁO TIẾN ĐỘ THÁNG 6") chiếm một ô duy
            # nhất ở trên. Header là dòng đầu tiên có từ hai ô có chữ trở lên.
            head_at = next(
                (
                    i
                    for i, row in enumerate(filled)
                    if sum(1 for c in row if c is not None and str(c).strip()) >= 2
                ),
                0,
            )
            sheets.append(
                Sheet(
                    name=worksheet.title,
                    header=[cell_text(c) for c in filled[head_at]],
                    rows=filled[head_at + 1 :],
                )
            )
        return sheets
    finally:
        # read_only giữ handle mở tới file zip; thiếu close là Windows không
        # xoá được file tạm ở `report`. Đóng cả hai — `workbook.close()` không
        # đóng handle mà mình tự mở.
        workbook.close()
        handle.close()


def _as_markdown(sheet: Sheet) -> str:
    """Một sheet -> bảng markdown.

    Render sang markdown chứ không dump text dẹt: chunk cắt ngang một bảng
    markdown vẫn còn dòng header ở đầu nhờ chunk_overlap, còn text dẹt thì
    chunk giữa bảng không còn gì cho biết mỗi cột là gì.
    """
    width = max([len(sheet.header)] + [len(row) for row in sheet.rows])

    def line(cells: list[str]) -> str:
        padded = cells + [""] * (width - len(cells))
        return "| " + " | ".join(padded) + " |"

    lines = [
        f"## {sheet.name}",
        "",
        line(sheet.header),
        "|" + "|".join(["---"] * width) + "|",
    ]
    lines += [line([cell_text(c) for c in row]) for row in sheet.rows]
    return "\n".join(lines)


def load_xlsx(path: Path) -> list[Document]:
    """Một sheet -> một Document, nội dung là bảng markdown.

    `metadata` chỉ có `sheet` và `source`: không còn `text_as_html` nhân bản
    cả bảng vào metadata của MỌI chunk rồi xuống cột `chunk_metadata`.
    """
    return [
        Document(
            page_content=_as_markdown(sheet),
            metadata={"sheet": sheet.name, "source": str(path)},
        )
        for sheet in read_workbook(path)
    ]


_BY_SUFFIX = {".txt": load_txt, ".xlsx": load_xlsx}
SUPPORTED = frozenset(_BY_SUFFIX)


def file_kind(file_name: str) -> str:
    """"BaoCao.XLSX" -> "xlsx". Khớp CHECK constraint của source_document."""
    return Path(file_name).suffix.lower().lstrip(".")


def load(path: Path, file_name: str | None = None) -> list[Document]:
    """Chọn loader theo đuôi rồi đọc.

    `file_name` tách khỏi `path` vì file tạm mang tên ngẫu nhiên không có đuôi —
    đuôi thật nằm ở tên gốc admin gửi lên.
    """
    suffix = Path(file_name or path).suffix.lower()
    loader = _BY_SUFFIX.get(suffix)
    if loader is None:
        raise UnsupportedFormat(
            f"Không đọc được {suffix or '(không có đuôi)'}; "
            f"nhận: {', '.join(sorted(SUPPORTED))}"
        )
    return loader(path)
