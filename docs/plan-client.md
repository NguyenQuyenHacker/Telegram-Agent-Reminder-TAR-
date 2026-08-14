# Kế hoạch luồng client — bot hỏi đáp trên kho tài liệu

> Bản song song của `docs/plan-ingest.md` (luồng admin nạp tài liệu).
> **Trạng thái: CHƯA cài.** Đây là thiết kế để đọc lại và chỉnh trước khi viết code.

---

## Context

Luồng admin (nạp tài liệu) đã xong: `graph_admin` đầy đủ 7 node, DB có `project` /
`source_document` / `doc_chunk` (pgvector 768 chiều + HNSW cosine), `TAR_agent/utils/embed.py`
có cả `embed_documents` lẫn `embed_query`, và `persistence/proc/chunks.py:search`
đã tra vector được.

Tầng Telegram cũng đã dựng sẵn **toàn bộ** phần client: `CLIENT_BOT_TOKEN`,
`BotRole CLIENT`, endpoint `/webhooks/telegram/client`, polling hai bot,
`thread_config` đã tách theo vai, `TAR_agent/utils/prompts/client_system.md` đã viết.

Thứ còn thiếu **chỉ nằm trong `TAR_agent/graph_client/`** (4 `NotImplementedError` + 2 thân
`@tool`) và ~15 dòng stub tạm trong `app/routers/webhooks.py` và `app/main.py` —
chính chúng ghi rõ "xoá nhánh tạm này khi graph_client xong".

**Kết quả mong muốn:** người dùng nhắn bot client một câu hỏi tiếng Việt, bot tự
quyết định tra dự án nào, tra bằng **hybrid search** (vector + full-text), rồi trả
lời kèm nguồn (tên file + mốc dữ liệu). Tra không ra thì nói thẳng kho không có.

---

## Quyết định kiến trúc

1. **Agent ⇄ tools (ReAct)**, không phải đường thẳng `retrieve → generate` như
   docstring `TAR_agent/graph_client/__init__.py` hiện viết. LLM tự gọi `list_projects` /
   `search_documents`, chặn số vòng bằng `agent.max_tool_rounds` trong
   `TAR_agent/utils/models.yaml`.
   → Hai stub `nodes/retrieve.py` và `nodes/generate.py` bị **xoá**, thay bằng
   `nodes/reset.py`, `nodes/agent.py`, `nodes/respond.py`.
2. **Tra cứu = hybrid search** đóng gói trong tool `search_documents`:
   vector (cosine) + full-text Postgres, hợp nhất bằng **RRF**.
3. **Ngưỡng khoảng cách cấu hình được** trong `models.yaml`; nhánh vector lọc
   theo ngưỡng, hai nhánh cùng rỗng ⇒ tool trả `status: "empty"`, LLM buộc phải
   nói "kho không có".
4. **Chọn dự án bằng tool thuần** — không thêm bàn phím inline, không interrupt.
   Mơ hồ thì LLM gọi `list_projects` rồi hỏi lại bằng chữ.
   → `handle_client_callback` giữ nguyên stub (client không có nút nào để bấm).

```
START → reset → agent ─ có tool_calls? ─ có → tools → agent
                  │                                     ↑
                  └─ không → respond → END        (≤ max_tool_rounds)
```

`agent` chỉ `bind_tools` khi `tool_call_rounds < max_tool_rounds`. Chạm trần thì
gọi LLM **không có tool** → model buộc phải chốt câu trả lời từ thứ đã tra được.
Cách này giữ lịch sử `messages` luôn hợp lệ: không bao giờ còn `tool_calls` treo
mà thiếu `ToolMessage` trả lời — để lọt là Gemini trả 400 ở **lượt sau**, một lỗi
rất khó lần vì nó không nổ ngay tại chỗ gây ra.

---

## Bước 1 — Full-text cho hybrid search (DB)

Thêm vào `persistence/schema/schema.sql` (cho DB dựng mới), và tạo file migration
rời `persistence/schema/migrate_hybrid.sql` với cùng nội dung (idempotent) để DB
đang chạy nâng cấp được mà không mất dữ liệu:

```sql
CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent() là STABLE (tra dictionary) nên không dùng thẳng trong GENERATED
-- column được. Bọc lại thành IMMUTABLE với dictionary chỉ đích danh.
CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS
$$ SELECT public.unaccent('public.unaccent', $1) $$;

-- 'simple' chứ không phải 'english': tiếng Việt không chia thì, stemmer tiếng
-- Anh chỉ cắt bậy. immutable_unaccent bỏ dấu ở CẢ HAI phía (nội dung và câu
-- hỏi) nên gõ "tien do" vẫn khớp "tiến độ".
ALTER TABLE doc_chunk ADD COLUMN IF NOT EXISTS content_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('simple', immutable_unaccent(content))) STORED;

CREATE INDEX IF NOT EXISTS idx_chunk_fts ON doc_chunk USING gin (content_tsv);
```

Cột GENERATED tự điền cho **cả dòng cũ lẫn dòng mới** ⇒ **không phải sửa
`TAR_agent/graph_admin/helpers/writer.py`, không phải nạp lại tài liệu**.

> Nếu Neon từ chối `CREATE EXTENSION unaccent`: bỏ `immutable_unaccent(...)`,
> dùng `to_tsvector('simple', content)`. Mất khả năng khớp không dấu, phần còn
> lại chạy y nguyên.

`persistence/models/doc_chunk.py` **không** khai `content_tsv`: truy vấn hybrid
dùng SQL thô, còn cột GENERATED mà để SQLModel biết thì nó sẽ tìm cách ghi vào.

---

## Bước 2 — `hybrid_search` trong proc

Thêm hàm mới vào `persistence/proc/chunks.py`, **bên cạnh** `search` hiện có (giữ
`search` để không phá gì đang chạy). Đồng bộ, nơi gọi bọc `asyncio.to_thread` như
mọi hàm khác trong file.

```python
@dataclass(frozen=True)
class HybridHit:
    chunk: DocChunk
    document: SourceDocument
    distance: float | None   # None = chỉ trúng nhánh từ khoá
    score: float             # điểm RRF

def hybrid_search(
    project_id: uuid.UUID,
    embedding: list[float],
    query_text: str,
    k: int = 5,
    *,
    max_distance: float = 0.6,
    candidates: int = 30,
    rrf_k: int = 60,
) -> list[HybridHit]:
```

Một truy vấn `text()` duy nhất (`persistence/proc/checkpoints.py` đã có tiền lệ
dùng SQL thô):

```sql
WITH vec AS (
    SELECT chunk_id,
           embedding <=> CAST(:emb AS vector) AS distance,
           row_number() OVER (ORDER BY embedding <=> CAST(:emb AS vector)) AS rnk
    FROM doc_chunk
    WHERE project_id = :pid
      AND embedding <=> CAST(:emb AS vector) <= :max_distance
    ORDER BY distance LIMIT :cand
),
kw AS (
    SELECT chunk_id,
           row_number() OVER (ORDER BY ts_rank_cd(content_tsv, q) DESC) AS rnk
    FROM doc_chunk, plainto_tsquery('simple', immutable_unaccent(:q)) AS q
    WHERE project_id = :pid AND content_tsv @@ q
    ORDER BY ts_rank_cd(content_tsv, q) DESC LIMIT :cand
),
fused AS (
    SELECT COALESCE(vec.chunk_id, kw.chunk_id) AS chunk_id,
           vec.distance,
           COALESCE(1.0/(:rrf_k + vec.rnk), 0) + COALESCE(1.0/(:rrf_k + kw.rnk), 0) AS score
    FROM vec FULL OUTER JOIN kw ON vec.chunk_id = kw.chunk_id
)
SELECT f.chunk_id, f.distance, f.score
FROM fused f ORDER BY f.score DESC, f.distance NULLS LAST LIMIT :k
```

Rồi nạp `DocChunk` + `SourceDocument` cho các `chunk_id` trúng bằng một `select()`
SQLModel thường — chọn cột tường minh, **không** `SELECT c.*`: cột `embedding` là
768 số float, lôi về mỗi lần tra là tốn băng thông cho thứ không ai dùng.

Hai điều dễ sai, ghi vào docstring:

- `<=>` phải giữ nguyên. Đổi sang `<->` hay `<#>` là Postgres bỏ index HNSW và
  quét toàn bảng — vẫn ra kết quả, chỉ chậm dần theo số dòng. (Cảnh báo này đã
  có sẵn ở `search`, lặp lại vì đây là hàm thứ hai chạm vào cùng cái index.)
- Ngưỡng `max_distance` chỉ chặn **nhánh vector**. Nhánh từ khoá đã tự chặn bằng
  `@@` — trúng từ khoá là tín hiệu thật, chặn thêm bằng khoảng cách ngữ nghĩa là
  vứt đúng thứ mà hybrid sinh ra để cứu: mã số, tên riêng, con số.

---

## Bước 3 — Tham số tra cứu

Thêm khối mới vào `TAR_agent/utils/models.yaml`:

```yaml
retrieval:
  k: 5              # số đoạn trả cho LLM
  candidates: 30    # số ứng viên mỗi nhánh trước khi hợp nhất
  max_distance: 0.6 # cosine; xa hơn coi như lạc đề
  rrf_k: 60         # hằng số RRF, 60 là giá trị chuẩn trong bài gốc
```

Rồi phơi ra ở `TAR_agent/utils/config.py`: `RETRIEVAL = _YAML["retrieval"]`.
**Không** thêm biến `.env` nào — `.env` chỉ chứa bí mật.

---

## Bước 4 — `TAR_agent/graph_client/helpers/retriever.py`

Giữ `RetrievedChunk` nhưng nới hai trường cho hợp hybrid:

```python
@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    heading_path: str | None
    file_name: str
    as_of_date: date
    distance: float | None   # None khi chỉ trúng từ khoá
    score: float             # điểm RRF

async def search(project_id: uuid.UUID, question: str, k: int | None = None
                ) -> list[RetrievedChunk]:
```

Thân hàm:

1. `vector = await embed_query(question)` — `TAR_agent/utils/embed.py`, đã dùng đúng
   `task_type=RETRIEVAL_QUERY`.
2. `hits = await asyncio.to_thread(chunks.hybrid_search, project_id, vector,
   question, k, max_distance=..., candidates=..., rrf_k=...)`, tham số lấy từ
   `RETRIEVAL` trong `TAR_agent/utils/config.py`.
3. Map sang `RetrievedChunk`. `heading_path` **suy từ** `chunk.chunk_metadata`
   (DB không có cột riêng): ưu tiên `sheet`, rồi `page`, rồi `source`; không có
   gì thì `None`.

`project_id` giữ nguyên là tham số đầu, không mặc định, không nhận `None`.

---

## Bước 5 — Tool (`TAR_agent/graph_client/tools/search.py`)

Hai tool, **chỉ đọc**, trả `dict` chứ không ném exception — LLM đọc `status` để tự
sửa hướng đi:

```python
@tool
async def list_projects() -> dict:
    """Liệt kê dự án trong kho, kèm số tài liệu và mốc dữ liệu mới nhất."""
    # -> {"projects": [{"name", "document_count", "latest_as_of"}]}

@tool
async def search_documents(project: str, query: str, k: int = 5) -> dict:
    """Tìm đoạn tài liệu liên quan trong phạm vi MỘT dự án."""
    # resolve_project(project)  ->  TAR_agent/utils/projects.py
    #   not_found  -> {"status": "project_not_found", "hint": "gọi list_projects..."}
    #   ambiguous  -> {"status": "ambiguous", "candidates": [...]}
    #   found      -> retriever.search(...)
    #                 rỗng -> {"status": "empty", "project": name}
    #                 có   -> {"status": "ok", "project": name,
    #                          "passages": [{"content", "file_name",
    #                                        "as_of_date", "heading_path"}]}
```

**Đổi kiểu trả về từ `list[dict]` sang `dict`** so với stub hiện tại: một `list`
rỗng không phân biệt được "dự án không tồn tại" với "dự án có nhưng không đoạn
nào khớp", mà hai thứ đó cần hai câu trả lời khác hẳn nhau.

**Không** trả `distance`/`score` cho LLM — model không dùng được con số đó vào
việc gì ngoài việc bịa ra một mức "độ tin cậy".

`CLIENT_TOOLS` trong `TAR_agent/graph_client/tools/__init__.py` giữ nguyên.

**Phụ thuộc nhỏ** — để `list_projects` giữ đúng lời hứa "mốc dữ liệu mới nhất":

- `persistence/proc/projects.py` `_list_with_counts`: thêm
  `func.max(SourceDocument.as_of_date)` vào `select` / `GROUP BY`.
- `TAR_agent/utils/projects.py` `ProjectBrief`: thêm `latest_as_of: date | None = None`.
  Có giá trị mặc định ⇒ chỗ dùng bên admin (`TAR_agent/graph_admin/nodes/ask_project.py`)
  không phải sửa.

---

## Bước 6 — Node + graph

Xoá `TAR_agent/graph_client/nodes/retrieve.py` và `TAR_agent/graph_client/nodes/generate.py`. Thêm ba file.

### `nodes/reset.py`

Dọn state đầu mỗi lượt MỚI, đúng lý do đã ghi trong `TAR_agent/graph_admin/nodes/route.py`:
các khoá ngoài `messages` không có reducer, không dọn thì `tool_call_rounds` của
lượt trước còn nguyên và lượt sau không bao giờ được gọi tool.

```python
def reset(state: ClientState) -> dict:
    return {"tool_call_rounds": 0, "outbox": [], "error": None}
```

### `nodes/agent.py` — chỗ **duy nhất** trong luồng client gọi LLM

```python
# TAR_agent/utils/config.py cấp hết: AGENT, load_prompt, now_local, client_model
async def agent(state: ClientState) -> dict:
    rounds = state.get("tool_call_rounds", 0)
    max_rounds = AGENT["max_tool_rounds"]

    system = load_prompt("client_system", TODAY=now_local().date().isoformat())

    history = state["messages"][-AGENT["max_history_messages"]:]
    # Cắt lịch sử phải bắt đầu từ một message HỢP LỆ: không được để ToolMessage
    # đứng đầu mà thiếu AIMessage sinh ra nó -> Gemini trả 400.

    llm = client_model(tools=CLIENT_TOOLS if rounds < max_rounds else None)
    reply = await llm.ainvoke({"system": [SystemMessage(system)], "messages": history})

    used = bool(getattr(reply, "tool_calls", None))
    return {"messages": [reply], "tool_call_rounds": rounds + 1 if used else rounds}
```

Bọc `try/except` → `{"error": "llm_failed"}`; `respond` dịch mã lỗi sang event.

### `nodes/respond.py` — `AIMessage` cuối → `outbox`

```python
{"kind": "answer", "data": {"text": ...}}                       # bình thường
{"kind": "client_failed", "data": {"reason": state["error"]}}   # có error
```

Chặn text rỗng (model trả chuỗi trắng) → `client_failed` với
`reason="empty_answer"`. Ở luồng này "im lặng" là kiểu hỏng tệ nhất: người dùng
hỏi xong ngồi chờ, và "bot chết" trông y hệt "bot đang nghĩ".

### Node `tools`

Không viết tay — dùng `ToolNode(CLIENT_TOOLS)` của `langgraph.prebuilt` (có sẵn
trong langgraph 0.2.60): nó tự bắt exception trong tool và trả `ToolMessage` lỗi
thay vì làm chết cả lượt.

### `state.py`

Bỏ `retrieved` (với kiến trúc tool, đoạn tra được nằm trong `ToolMessage` chứ
không trong state), thêm `error: str | None`.

> **Đánh đổi cần ghi rõ trong docstring.** Quy tắc cũ ghi ở `nodes/generate.py`
> — "`retrieved` rỗng ⇒ KHÔNG gọi LLM" — không còn cưỡng chế được ở tầng node,
> vì LLM đã ở trong vòng lặp *trước khi* biết tra được gì. Thay bằng hai lớp:
> tool trả `status: "empty"` tường minh, và `TAR_agent/utils/prompts/client_system.md`
> đã bắt buộc "nói thẳng kho không có". **Cần siết thêm một câu vào prompt:**
> gặp `status: "empty"` thì trả lời đúng một câu là kho không có, cấm suy diễn.

### `graph.py`

```python
def build_client_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(ClientState)
    builder.add_node("reset", reset)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(CLIENT_TOOLS))
    builder.add_node("respond", respond)

    builder.add_edge(START, "reset")
    builder.add_edge("reset", "agent")
    builder.add_conditional_edges("agent", choose_branch,
                                  {"tools": "tools", "respond": "respond"})
    builder.add_edge("tools", "agent")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)
```

`graph_client` **không** import bất cứ thứ gì từ `graph_admin` — ranh giới quyền
giữ nguyên là ranh giới thật chứ không phải quy ước.

---

## Bước 7 — Nối vào tầng Telegram

### `app/main.py`

Thay `app.state.client_graph = None` bằng `build_client_graph(checkpointer)` (dùng
chung checkpointer, `thread_id` đã tách theo vai) và xoá khối comment
"graph_client CHƯA xây".

### `app/routers/webhooks.py`

- Tổng quát hoá `_noi(chat_id, event)` → `_noi(role: BotRole, chat_id, event)` và
  `_bao_hong(role, chat_id)`; sửa các chỗ gọi bên admin. Hiện `_noi` chốt cứng
  `ADMIN.bot` — đúng thứ mà docstring `app/telegram/sender.py` cảnh báo: "lấy
  nhầm là câu trả lời của client đi ra từ bot admin".
- Thêm `_CLIENT_SLOTS = asyncio.Semaphore(4)` riêng. **Không** dùng lại
  `_INGEST_SLOTS`: nó chặn ở 2 để giữ pool ghi Postgres, còn hỏi đáp chủ yếu chờ
  API Gemini, chặn chung là hai loại tải khác hẳn nhau tranh một cái van.
- Viết lại `handle_client_message` theo đúng khuôn `handle_admin_message`:

```python
chat_id = message.chat.id
if message.document:  # giữ nguyên câu từ chối
    ...; return
text = (message.text or message.caption or "").strip()
if not text or text in ("/start", "/help"):
    await _noi(CLIENT, chat_id, {"kind": "client_hint", "data": {}}); return
try:
    async with chat_lock(CLIENT, chat_id), _CLIENT_SLOTS:
        config = thread_config(CLIENT, chat_id)
        out = await app.state.client_graph.ainvoke(
            {"messages": [HumanMessage(text)]}, config)
        for event in out.get("outbox", []):
            await _noi(CLIENT, chat_id, event)
except Exception:
    log.exception("Lượt client thất bại (chat %s)", chat_id)
    await _bao_hong(CLIENT, chat_id)
```

**Không** gọi `pending_interrupt`: graph client không có `interrupt()` nào. Quy
tắc "mọi đường ra khỏi handler đều phải nói một câu gì đó" giữ nguyên.

- `handle_client_callback` giữ nguyên stub, chỉ sửa docstring cho khỏi nói là
  "chưa xây".

### `app/telegram/render.py`

Thêm vào `_RENDER`:

- `answer` → trả `data["text"]` nguyên văn; cắt ở ~4000 ký tự kèm "…" (giới hạn
  Telegram là 4096; `sender.py` gửi text thuần nên không phải escape gì).
- `client_hint` → "Hỏi mình về tài liệu dự án. Ví dụ: *tiến độ dự án X tuần này?*
  Chưa rõ dự án nào thì cứ hỏi, mình liệt kê ra."
- `client_failed` → bảng mã lỗi: `llm_failed`, `empty_answer`.

---

## Bước 8 — Test

### `tests/conftest.py` — thêm, theo đúng lối đã có

- `FakeKho` mở rộng: `embed_query`, `hybrid_search` trả `HybridHit` dựng sẵn,
  `resolve_project` ba trạng thái, đếm số lần gọi.
- `kho_client(monkeypatch)` — vá **tại nơi dùng** bằng `importlib.import_module`
  (`nodes/__init__.py` xuất lại HÀM trùng tên với MODULE, viết
  `from graph_client.nodes import agent` sẽ trả về hàm và `setattr` nổ
  `AttributeError`).
- `FakeLLM` — Runnable kịch bản: nhận trước một danh sách `AIMessage` (có/không
  `tool_calls`) rồi trả lần lượt. Vá `client_model` trong
  `TAR_agent.graph_client.nodes.agent`.
- `client_graph` fixture = `build_client_graph(MemorySaver())`.

### `tests/test_client_graph.py` (mới)

Dùng **một `thread_id` cho nhiều lượt** (`client:<tên>`), đúng bài học đã ghi
trong `test_admin_graph.py`:

- Hỏi rõ tên dự án → đúng một vòng tool → outbox có `answer`.
- Dự án mơ hồ → LLM gọi `list_projects` → hỏi lại → lượt sau gõ tên → tra được.
- Tra rỗng (`status: "empty"`) → câu trả lời không bịa; assert **không** có lời
  gọi nào tới hàm ghi.
- **Chạm trần vòng lặp**: hết `max_tool_rounds` vẫn phải ra `answer`, và
  `messages` cuối cùng **không** còn `tool_calls` treo.
- **Reset giữa hai lượt**: lượt 1 dùng hết vòng, lượt 2 vẫn gọi tool được — đây
  chính là lỗi mà `nodes/reset.py` sinh ra để chặn.

### `tests/test_client_tools.py` (mới)

`search_documents` với `not_found` / `ambiguous` / `empty` / `ok`; khẳng định
`project_id` **luôn** được truyền xuống `retriever.search`, không bao giờ tra
toàn kho.

### `tests/test_telegram_layer.py`

Thêm các kind mới vào bộ tham số render: mọi kind phải ra chuỗi khác rỗng, kind
lạ không được ném.

---

## Kiểm chứng

**1. DB** — `psql "$DATABASE_URL" -f persistence/schema/migrate_hybrid.sql`, rồi:

```sql
SELECT count(*) FILTER (WHERE content_tsv IS NULL) FROM doc_chunk;  -- kỳ vọng 0
EXPLAIN ANALYZE SELECT chunk_id FROM doc_chunk
  WHERE content_tsv @@ plainto_tsquery('simple', immutable_unaccent('tien do'));
-- kỳ vọng: Bitmap Index Scan trên idx_chunk_fts
```

**2. Test** — `python -m pytest tests -q`, chạy **toàn bộ** chứ không chỉ file
mới: phải chắc việc đổi `_noi` và `ProjectBrief` không phá luồng admin.

**3. Thủ công, hai bot cùng lúc** (`TELEGRAM_WEBHOOK_URL` trống ⇒ polling) —
`python run.py`, rồi:

- Bot **admin**: `/duan Nhà máy A` → gửi một file `.txt` → chọn dự án → "✓ Đã nạp".
- Bot **client**: hỏi một câu có nội dung nằm trong file đó → câu trả lời phải
  kèm **tên file + mốc dữ liệu**.
- Bot client: hỏi một câu chắc chắn không có trong kho → phải nói kho không có,
  **không** được suy diễn.
- Bot client: hỏi cộc lốc không nêu dự án → phải liệt kê dự án và hỏi lại.
- Bot client: gửi một file → "Bot này chỉ trả lời câu hỏi, không nhận file."
- Bot client: gõ một từ khoá hiếm / mã số có trong file (thứ vector hay trượt) →
  phải tra ra. Đây là chỗ nhánh full-text chứng minh giá trị của nó.

**4. Ranh giới quyền** — `grep -rn "graph_admin" TAR_agent/graph_client/` phải **không có
kết quả nào**.

---

## Ngoài phạm vi

- Bàn phím inline / `interrupt()` cho luồng client (đã chốt: tool thuần).
- Re-ranker (cross-encoder) sau RRF.
- Dùng LLM cho `handle_text` bên admin — nhánh đó vẫn ba lệnh cố định.
- Sửa `README.md` / `docs/todo.md` đang lỗi thời.
