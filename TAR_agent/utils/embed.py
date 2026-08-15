"""Sinh vector — chỗ duy nhất trong repo gọi API embedding.

`task_type` khác nhau hai đầu: RETRIEVAL_DOCUMENT lúc nạp, RETRIEVAL_QUERY lúc
hỏi. Dùng nhầm vẫn chạy, vẫn ra số, chỉ là tìm kiếm tệ đi mà không ai biết.

Dùng bản đồng bộ bọc to_thread: bản async của langchain-core bỏ qua
`output_dimensionality`, mà đó là thứ cắt 3072 chiều xuống EMBEDDING_DIM.
"""

import asyncio

from TAR_agent.utils.config import EMBEDDING_DIM, embedding_model, load_config

EMBEDDING = load_config()["embedding"]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Vector cho chunk lúc NẠP. Chia lô tuần tự để khỏi dính rate limit."""
    if not texts:
        return []

    client = embedding_model()
    batch_size = EMBEDDING["batch_size"]
    vectors: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        vectors.extend(
            await asyncio.to_thread(
                client.embed_documents,
                texts[start : start + batch_size],
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=EMBEDDING_DIM,
            )
        )

    # Lệch số lượng là hỏng ngầm tệ nhất: chunk thứ n mang vector của chunk thứ
    # m, tra ra kết quả sai mà không lỗi nào nổ.
    if len(vectors) != len(texts):
        raise RuntimeError(f"Nhận {len(vectors)} vector cho {len(texts)} đoạn")
    if any(len(v) != EMBEDDING_DIM for v in vectors):
        raise RuntimeError(f"Vector không đủ {EMBEDDING_DIM} chiều")

    return vectors


def embed_query_sync(text: str) -> list[float]:
    """Vector cho câu hỏi lúc TRA. CHẶN event loop — chỉ gọi từ trong thread.

    Có bản đồng bộ vì tầng retriever của LangChain (`BaseRetriever`,
    `EnsembleRetriever`) là đồng bộ: cả vòng tra cứu đã nằm gọn trong MỘT
    `asyncio.to_thread`, gọi bản async ở giữa là phải dựng thêm một event loop
    lồng trong thread đó.
    """
    return embedding_model().embed_query(
        text,
        task_type="RETRIEVAL_QUERY",
        output_dimensionality=EMBEDDING_DIM,
    )
