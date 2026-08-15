"""Tìm chunk liên quan nhất trong phạm vi MỘT dự án. Hybrid BM25 + vector.

`project_id` là tham số BẮT BUỘC, không có giá trị mặc định, không nhận None.
Bỏ lọc dự án là trả lời câu hỏi của dự án này bằng tài liệu của dự án khác — và
câu trả lời đó trông vẫn rất thật.

VÌ SAO HAI NHÁNH chứ không chỉ vector:
  - Vector bắt được câu hỏi diễn đạt khác hẳn chữ trong file ("chậm tiến độ" so
    với "trễ so với kế hoạch").
  - BM25 bắt được thứ vector luôn làm hỏng: mã hiệu ("HM3", "QĐ-142/2026"), tên
    riêng, con số. Embedding kéo "HM3" và "HM7" về gần như cùng một điểm.

Hợp nhất bằng RRF của `EnsembleRetriever` — xếp hạng lại theo THỨ HẠNG ở mỗi
nhánh, nên không phải chuẩn hoá điểm cosine với điểm BM25 (hai thang đo không
so được với nhau).

TOÀN BỘ module này ĐỒNG BỘ, trừ `search`. Tầng retriever của LangChain là đồng
bộ, chỉ mục BM25 nằm trong RAM và tokenize là việc nặng CPU — gói cả cụm vào
một `asyncio.to_thread` thay vì rải async nửa vời từng lớp.
"""

import asyncio
import logging
import threading
import uuid
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from langchain.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from persistence.models import DocChunk, SourceDocument
from persistence.proc import chunks as proc
from TAR_agent.utils.config import load_config
from TAR_agent.utils.embed import embed_query_sync
from TAR_agent.utils.text import ascii_lower

log = logging.getLogger(__name__)

RETRIEVAL = load_config()["retrieval"]


@dataclass(frozen=True)
class RetrievedChunk:
    """Một đoạn tra được, kèm đủ thứ để trích dẫn nó.

    `distance` là cosine distance của nhánh vector, `None` khi đoạn này do BM25
    đưa lên trước (RRF giữ bản gặp đầu tiên). `score` là điểm RRF sau hợp nhất
    — chỉ so sánh được TRONG cùng một lần tra, không phải một thang tuyệt đối.
    """

    content: str
    heading_path: str | None
    file_name: str
    as_of_date: date
    distance: float | None
    score: float


def _heading_path(chunk_metadata: dict[str, Any]) -> str | None:
    """Vị trí của đoạn trong file, để câu trả lời chỉ được chỗ mà tra lại.

    Thứ tự ưu tiên bám theo loader: `.xlsx` cho ra `sheet`, PDF cho ra `page`,
    còn `source` là đường dẫn file tạm — dùng làm nước cuối vì có còn hơn không.
    """
    for key in ("sheet", "page", "source"):
        value = chunk_metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _to_document(
    chunk: DocChunk, document: SourceDocument, distance: float | None = None
) -> Document:
    """Chỗ DUY NHẤT dựng Document, cho cả hai nhánh.

    Hai nhánh phải sinh `page_content` GIỐNG HỆT nhau: RRF gộp trùng theo đúng
    chuỗi này. Lệch một khoảng trắng là một đoạn tra được ở cả hai nhánh bị đếm
    thành hai đoạn khác nhau, ăn hai suất trong `k` và đẩy đoạn khác ra ngoài.
    """
    metadata: dict[str, Any] = {
        "chunk_id": str(chunk.chunk_id),
        "file_name": document.file_name,
        "as_of_date": chunk.as_of_date.isoformat(),
        "heading_path": _heading_path(chunk.chunk_metadata),
    }
    # Chỉ nhánh vector có khoảng cách. Để ngoài 4 khoá trên vì nó là số liệu
    # chẩn đoán, không phải thứ đi kèm đoạn văn.
    if distance is not None:
        metadata["distance"] = float(distance)
    return Document(page_content=chunk.content, metadata=metadata)


class PgVectorRetriever(BaseRetriever):
    """Vỏ BaseRetriever bọc `persistence.proc.chunks.search`.

    Có ngưỡng `max_distance` vì kho luôn trả về đủ `k` đoạn gần nhất kể cả khi
    chẳng đoạn nào liên quan: hỏi "giá vàng hôm nay" trong kho tiến độ xây dựng
    vẫn ra 5 đoạn. Đưa 5 đoạn lạc đề cho LLM là mời nó tán.
    """

    project_id: uuid.UUID
    k: int
    max_distance: float

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        vector = embed_query_sync(query)
        rows = proc.search(self.project_id, vector, self.k)
        return [
            _to_document(chunk, document, distance)
            for chunk, document, distance in rows
            if distance <= self.max_distance
        ]


# project_id -> (fingerprint lúc dựng, chỉ mục). OrderedDict để đuổi bản cũ nhất
# khi vượt `cache_projects` — chỉ mục sống trong RAM của tiến trình, không có
# trần thì mỗi dự án từng được hỏi là một bản sao kho nằm lại vĩnh viễn.
_BM25_CACHE: "OrderedDict[uuid.UUID, tuple[tuple[int, datetime | None], BM25Retriever]]" = (
    OrderedDict()
)
# Nhiều lượt hỏi chạy song song, mỗi lượt một thread của asyncio.to_thread.
_CACHE_LOCK = threading.Lock()


def _tokenize(text: str) -> list[str]:
    """Bỏ dấu rồi cắt theo khoảng trắng.

    Bỏ dấu là bắt buộc chứ không phải tiện tay: người dùng Telegram gõ "tien do
    hang muc 3" còn file viết "tiến độ hạng mục 3". Không chuẩn hoá thì nhánh
    BM25 im lặng không khớp gì cả, và không có lỗi nào nổ để biết.
    """
    return ascii_lower(text).split()


def _bm25_for(project_id: uuid.UUID) -> BM25Retriever | None:
    """Chỉ mục BM25 của một dự án. None khi kho của dự án đó rỗng.

    Dựng chỉ mục BÊN NGOÀI khoá: đọc cả kho rồi tokenize là việc nặng, giữ khoá
    suốt quá trình đó là mọi lượt hỏi của mọi dự án xếp hàng sau nó. Cái giá:
    hai lượt cùng gặp cache lạnh của cùng một dự án sẽ dựng hai lần rồi ghi đè
    nhau — tốn công một lần, không sai kết quả.
    """
    fingerprint = proc.corpus_fingerprint(project_id)

    with _CACHE_LOCK:
        cached = _BM25_CACHE.get(project_id)
        if cached is not None and cached[0] == fingerprint:
            _BM25_CACHE.move_to_end(project_id)
            return cached[1]

    rows = proc.load_corpus(project_id)
    if not rows:
        return None

    log.info("Dựng chỉ mục BM25 cho dự án %s (%d đoạn)", project_id, len(rows))
    retriever = BM25Retriever.from_documents(
        [_to_document(chunk, document) for chunk, document in rows],
        preprocess_func=_tokenize,
    )
    retriever.k = RETRIEVAL["bm25_k"]

    with _CACHE_LOCK:
        _BM25_CACHE[project_id] = (fingerprint, retriever)
        _BM25_CACHE.move_to_end(project_id)
        while len(_BM25_CACHE) > RETRIEVAL["cache_projects"]:
            _BM25_CACHE.popitem(last=False)
    return retriever


class _ScoredEnsemble(EnsembleRetriever):
    """EnsembleRetriever nhưng ghi điểm RRF vào `metadata["score"]`.

    Bản gốc xếp hạng theo điểm rồi vứt điểm đi, chỉ trả về danh sách đã sắp.
    Node `grade` và log chẩn đoán cần con số đó — không có nó thì "đoạn này hơn
    đoạn kia bao nhiêu" là không trả lời được.
    """

    def weighted_reciprocal_rank(self, doc_lists: list[list[Document]]) -> list[Document]:
        scores: dict[str, float] = defaultdict(float)
        for doc_list, weight in zip(doc_lists, self.weights):
            for rank, doc in enumerate(doc_list, start=1):
                scores[doc.page_content] += weight / (rank + self.c)

        fused = super().weighted_reciprocal_rank(doc_lists)
        for doc in fused:
            doc.metadata["score"] = scores[doc.page_content]
        return fused


def _hybrid(project_id: uuid.UUID) -> BaseRetriever:
    """BM25 + vector, hoặc chỉ vector khi kho của dự án chưa có chunk nào.

    Thứ tự `[bm25, vector]` phải khớp thứ tự trong `weights` ở models.yaml. Đảo
    một trong hai là đổi trọng số của cả hệ mà không có gì báo.
    """
    vector = PgVectorRetriever(
        project_id=project_id,
        k=RETRIEVAL["vector_k"],
        max_distance=RETRIEVAL["max_distance"],
    )
    bm25 = _bm25_for(project_id)
    if bm25 is None:
        return vector
    return _ScoredEnsemble(retrievers=[bm25, vector], weights=RETRIEVAL["weights"])


def _search_sync(project_id: uuid.UUID, question: str, k: int) -> list[RetrievedChunk]:
    documents = _hybrid(project_id).invoke(question)[:k]
    return [
        RetrievedChunk(
            content=doc.page_content,
            heading_path=doc.metadata.get("heading_path"),
            file_name=doc.metadata.get("file_name", ""),
            as_of_date=date.fromisoformat(doc.metadata["as_of_date"]),
            distance=doc.metadata.get("distance"),
            # Chỉ một nhánh chạy (kho rỗng chunk BM25) thì không có điểm RRF;
            # rơi về nghịch đảo thứ hạng để `score` luôn giảm dần như hợp đồng.
            score=float(doc.metadata.get("score", 1.0 / rank)),
        )
        for rank, doc in enumerate(documents, start=1)
    ]


async def search(
    project_id: uuid.UUID, question: str, k: int | None = None
) -> list[RetrievedChunk]:
    """Đoạn liên quan nhất trong kho của MỘT dự án, điểm giảm dần."""
    return await asyncio.to_thread(
        _search_sync, project_id, question, k or RETRIEVAL["k"]
    )
