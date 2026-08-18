"""Đồng hồ bấm giờ cho từng chặng. Log ra ms, không giữ số liệu ở đâu cả.

Trước file này cả repo không có một dòng đo thời gian nào, nên "chặng nào chậm"
vẫn là phỏng đoán đọc bằng mắt từ một trace. Mọi quyết định tối ưu độ trễ phải
ra từ log ở đây.

Hai cách dùng:

    async with track("retrieve.table") as span:   # đo một khối code
        span["rows"] = len(rows)

    builder.add_node("compose", timed_node("compose", self._compose))
"""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)

# Khoá trong bản cập nhật của node đáng đi kèm số ms. Toàn bộ là bộ đếm vòng lặp
# và cờ rẽ nhánh: đuôi 30s sinh ra từ một lượt đi hai vòng, không phải từ một
# chặng chậm bất thường — nhìn ms mà không nhìn mấy con số này thì không phân
# biệt được hai chuyện đó.
_TRACKED = ("agent_action", "verdict", "iteration_count", "revise_count", "attempt")


@asynccontextmanager
async def track(label: str, **fields: Any) -> AsyncIterator[dict[str, Any]]:
    """Đo wall-clock một khối code. Ghi thêm số liệu vào dict được yield ra.

    Log CẢ KHI NÉM: chặng chậm nhất thường là chặng hỏng, mà chặng hỏng thì
    không bao giờ chạy tới một dòng log đặt ở cuối hàm.
    """
    span: dict[str, Any] = dict(fields)
    start = time.perf_counter()
    failed = False
    try:
        yield span
    except BaseException:
        failed = True
        raise
    finally:
        parts = [f"{key}={value}" for key, value in span.items()]
        if failed:
            parts.append("FAILED")
        log.info(
            "%s %dms%s",
            label,
            round((time.perf_counter() - start) * 1000),
            (" · " + " ".join(parts)) if parts else "",
        )


def timed_node(
    label: str, node: Callable[[Any], Awaitable[dict]]
) -> Callable[[Any], Awaitable[dict]]:
    """Bọc một node LangGraph bằng đồng hồ.

    Dùng ở `build()` chứ không rải vào thân từng node: một chỗ duy nhất biết về
    đo đạc, và thân node giữ nguyên đúng việc của nó.
    """

    async def timed(state: Any) -> dict:
        async with track(label) as span:
            update = await node(state)
            span.update({k: v for k, v in (update or {}).items() if k in _TRACKED})
            return update

    timed.__name__ = f"timed_{label.replace('.', '_')}"
    return timed
