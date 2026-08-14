"""list[Document] -> chunk nhỏ để embed. Hàm thuần, không I/O.

Dùng `split_documents` chứ không `split_text`: nó chép metadata của Document gốc
sang mọi chunk con, nhờ vậy tên sheet còn nguyên tới lúc trích nguồn.
"""

from functools import lru_cache

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from TAR_agent.utils.config import CHUNKING


@lru_cache(maxsize=1)
def _splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(**CHUNKING)


def split_documents(documents: list[Document]) -> list[Document]:
    """Cắt và bỏ chunk chỉ có khoảng trắng — chúng tốn một lượt embedding và một
    dòng DB mà không bao giờ là câu trả lời cho ai."""
    return [c for c in _splitter().split_documents(documents) if c.page_content.strip()]
