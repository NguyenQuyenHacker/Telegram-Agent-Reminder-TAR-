"""Đường dẫn file -> list[Document]. Một cửa vào duy nhất: `load()`.

Hàm ĐỒNG BỘ và nặng: nơi gọi bọc asyncio.to_thread, chạy thẳng trên event loop
là treo cả hai bot.

⚠ Phần .xlsx CHƯA kiểm chứng bằng dữ liệu thật — `unstructured[xlsx]` cần
`pandas` (chưa có trong tar_env) và `python-magic` (thiếu libmagic trên Windows
là ImportError ngay lúc gọi .load()).
"""

from pathlib import Path

from langchain_community.document_loaders import TextLoader, UnstructuredExcelLoader
from langchain_core.documents import Document

# Thứ tự quan trọng: utf-8-sig trước để nuốt BOM, cp1258 cuối vì nó decode được
# gần như mọi chuỗi byte — đặt sớm là nó nuốt luôn file UTF-8 và trả tiếng Việt
# sai bét mà không báo lỗi.
_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "cp1258")


class UnsupportedFormat(Exception):
    """Đuôi file không nằm trong SUPPORTED."""


def load_txt(path: Path) -> list[Document]:
    """Một file -> một Document. Dò bảng mã tay để khỏi thêm `chardet`."""
    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            return TextLoader(str(path), encoding=encoding).load()
        except (UnicodeDecodeError, RuntimeError) as exc:
            last_error = exc  # TextLoader gói UnicodeDecodeError vào RuntimeError
    raise ValueError(f"Không đọc được bảng mã của {path.name}") from last_error


def load_xlsx(path: Path) -> list[Document]:
    """Một sheet -> một Document. Đổi metadata "page_name" thành "sheet"."""
    docs = UnstructuredExcelLoader(str(path), mode="elements").load()
    result = []
    for doc in docs:
        if not doc.page_content.strip():
            continue
        metadata = dict(doc.metadata)
        if "page_name" in metadata:
            metadata["sheet"] = metadata.pop("page_name")
        metadata["source"] = str(path)
        result.append(Document(page_content=doc.page_content, metadata=metadata))
    return result


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
