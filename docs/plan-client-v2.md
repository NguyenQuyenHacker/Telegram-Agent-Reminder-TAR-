# Kế hoạch — graph_client: identify_project + ReAct + subgraph hybrid search

## Context

`graph_admin` (nạp tài liệu) đã chạy đủ 7 node. Tầng Telegram, DB, embedding,
checkpointer đã dựng sẵn hết. Chỗ trống nằm trong `TAR_agent/graph_client/`
(4 `NotImplementedError` + 2 thân `@tool`) và ~15 dòng stub trong `app/main.py`
/ `app/routers/webhooks.py` — chính chúng ghi "xoá nhánh tạm này khi
graph_client xong".

Cần dựng: bot client nhận câu hỏi tiếng Việt → một node LLM rẻ xác định dự án →
agent ReAct tra kho bằng hybrid search có vòng tự sửa truy vấn → soạn câu trả
lời rồi tự rà soát số liệu và dẫn chiếu trước khi gửi.

### Graph

```
START → reset → identify_project ─ có project_id ──→ agent
                       │                               │
                       └─ không ──→ respond → END      ├─ có tool_calls → tools → agent
                                                       └─ hết tool_calls → compose
                                                                             │
                                              ┌── "thieu" ──────────────────┤
                                              └─→ agent          "dat" ─────→ respond → END
```

Subgraph nằm TRONG tool `search_docs`, graph ngoài không biết:

```
retrieve → grade ─ đạt ─────────────────→ trả passages
             ├─ không đạt, attempt<2 ──→ rewrite → retrieve
             └─ hết lượt ──────────────→ trả status "empty"
```

Guardrail: `max_iterations 6` · `max_revise 2` · `max_tool_rounds 3` ·
`attempt 2`.

### Đã chốt

| Điểm | Quyết định |
|---|---|
| Xác định dự án | Node LLM `identify_project`, không fuzzy match, không rapidfuzz |
| Model node đó | Khối `identify` riêng: `gemini-2.5-flash-lite`, temperature 0 |
| Gọi mỗi lượt | Có — không nhánh tắt, một nguồn duy nhất |
| Câu hỏi gốc | Giữ **cả hai**: `messages` nguyên văn, `remaining_question` vào system prompt |
| Câu hỏi ngoài phạm vi dự án | Xử bằng **luật trong system prompt**, không thêm trường, không thêm code |
| project_id → tool | `InjectedState`, LLM không sinh được |
| Tool | Chỉ `search_docs`. Chưa khai `query_project_db` / `calc_ratio` |
| BM25 | Nạp theo dự án từ DB + cache, vô hiệu bằng fingerprint |
| grade | 1 lượt LLM chấm cả lô |
| Chống bịa số | Prompt chốt định dạng + regex mỏng cưỡng chế |
| Dựng graph | **Class**, mỗi LLM một thuộc tính có tên nói rõ việc nó làm |
| Test | Chỉ tạo file rỗng, viết sau |
| Cài gói | Chỉ sửa `requirements.txt`. **Không tự chạy `pip install`** |

---

# PHẦN 1 — CHECKLIST

## Bước 0 — Phụ thuộc, tham số, factory model

- [ ] `requirements.txt`: thêm hai dòng. Không chạy `pip` — bạn tự cài rồi báo lại.

```
langchain>=0.3.14,<0.4
rank_bm25==0.2.2
```

- [ ] `TAR_agent/utils/models.yaml`: một khối model cho **mỗi** chỗ gọi LLM,
      thêm khối `retrieval`, thêm hai khoá vào `agent`.

```yaml
models:
  admin:      # giữ nguyên
  client:     # giữ nguyên — dùng cho node agent
  identify:
    model: gemini-2.5-flash-lite
    temperature: 0.0
    timeout: 15
    max_retries: 2
  compose:
    model: gemini-2.5-flash
    temperature: 0.1
    timeout: 45
    max_retries: 2
  grade:
    model: gemini-2.5-flash-lite
    temperature: 0.0
    timeout: 20
    max_retries: 2
  rewrite:
    model: gemini-2.5-flash-lite
    temperature: 0.3
    timeout: 15
    max_retries: 2

retrieval:
  bm25_k: 5
  vector_k: 5
  weights: [0.4, 0.6]     # [bm25, vector]
  k: 5
  max_distance: 0.6
  max_attempts: 2
  cache_projects: 8

agent:
  max_tool_rounds: 3
  max_history_messages: 20
  max_iterations: 6
  max_revise: 2
```

- [ ] `TAR_agent/utils/config.py`:
  - Đổi `_chat_model` thành hàm công khai
    `create_google_genai(chat_config, *, tools=None, output_schema=None) -> Runnable`.
  - Thêm `load_config() -> dict` trả nguyên `_YAML` (graph tự lấy khối nó cần,
    không phải import 5 hằng số rời).
  - Giữ `admin_model` / `client_model` làm vỏ mỏng gọi `create_google_genai`
    để không phá chỗ nào đang dùng.
  - Thêm `RETRIEVAL = _YAML["retrieval"]`. Cập nhật `__all__`.

**Verify**
```bash
PY="D:/QUYEN/DI_LAM/miniconda3/envs/tar_env/python.exe"
"$PY" -c "from langchain.retrievers import EnsembleRetriever; from langchain_community.retrievers import BM25Retriever; import rank_bm25; print('ok')"
"$PY" -c "from TAR_agent.utils.config import load_config, create_google_genai, RETRIEVAL; c=load_config(); print(sorted(c['models']), RETRIEVAL)"
```

## Bước 1 — Tầng truy xuất (không LLM, không graph)

- [ ] `persistence/proc/chunks.py` — hai hàm đồng bộ mới, giữ nguyên `search`:
  - `corpus_fingerprint(project_id) -> tuple[int, datetime | None]`
    — `count(*)` và `max(source_document.uploaded_at)` của dự án.
  - `load_corpus(project_id) -> list[tuple[DocChunk, SourceDocument]]`
    — **chọn cột tường minh**, không lấy `embedding`.
- [ ] `TAR_agent/graph_client/helpers/retriever.py` — viết lại:
  - `RetrievedChunk` thêm `score: float`, đổi `distance: float | None`.
  - `PgVectorRetriever(BaseRetriever)` bọc `chunks.search`, lọc `max_distance`.
  - `_bm25_for(project_id)` — cache `dict[uuid, (fingerprint, BM25Retriever)]`,
    LRU `cache_projects`, `preprocess_func=lambda s: ascii_lower(s).split()`.
  - `_hybrid(project_id) -> EnsembleRetriever([bm25, vector], weights=…)`.
  - `async def search(project_id, question, k=None) -> list[RetrievedChunk]`
    — bọc `asyncio.to_thread`.
  - Hai nhánh sinh `Document` **giống hệt nhau**: `page_content=chunk.content`,
    `metadata={"chunk_id","file_name","as_of_date","heading_path"}`.
  - `heading_path` suy từ `chunk.chunk_metadata`: `sheet` → `page` → `source` →
    `None`.

**Verify** — script trong scratchpad, DB thật đã có 1 dự án:
```python
hits = await search(pid, "tiến độ")   # in file_name/as_of_date/distance/score
                                      # score giảm dần, len(hits) <= k
```

## Bước 2 — Subgraph của `search_docs`

- [ ] Thư mục mới `TAR_agent/graph_client/subgraph/`:
      `__init__.py`, `state.py`, `nodes.py`, `graph.py`.
- [ ] `SearchState`: `question`, `original`, `project_id`, `attempt`, `docs`,
      `ok`.
- [ ] `graph.py` — class, LLM khai báo tên rõ:

```python
class SearchGraph:
    """Vòng tra cứu tự sửa truy vấn. Compile một lần, không checkpointer."""

    def __init__(self) -> None:
        config = load_config()
        models = config["models"]
        self.retrieval = config["retrieval"]
        self.max_attempts = self.retrieval["max_attempts"]

        self.doc_grader = create_google_genai(models["grade"], output_schema=Grade)
        self.query_rewriter = create_google_genai(models["rewrite"])

    async def _retrieve(self, state): ...
    async def _grade(self, state): ...
    async def _rewrite(self, state): ...
    def _after_grade(self, state) -> str: ...
    def build(self): ...

SEARCH_GRAPH = SearchGraph().build()   # module level, dựng một lần
```

- [ ] `_retrieve` — gọi `helpers.retriever.search`, `attempt += 1`.
- [ ] `_grade` — 1 lượt LLM,
      `Grade{ keep: list[int]; enough: bool; reason: str }`. Chấm theo
      `original`. Lọc `docs` xuống còn `keep`.
- [ ] `_rewrite` — LLM đổi từ khoá, trả chuỗi thuần, ghi vào `question`.
- [ ] `_after_grade`: `ok` → END; `attempt >= max_attempts` → END với
      `docs=[]`; còn lại → `rewrite`.
- [ ] Prompt: `TAR_agent/utils/prompts/client_system/grade_docs.md`,
      `client_system/rewrite_query.md`.

**Verify** — script gọi thẳng `SEARCH_GRAPH.ainvoke({...})`, in `attempt`,
`len(docs)`, `question`. Ba ca: đạt ngay / đạt lần 2 / trượt cả hai (phải
`docs == []`, `attempt == 2`, không rewrite lần 3).

## Bước 3 — Tool

- [ ] `TAR_agent/graph_client/tools/search.py` — xoá `list_projects` và
      `search_documents`, thay bằng một tool:

```python
@tool
async def search_docs(
    query: str,
    project_id: Annotated[uuid.UUID, InjectedState("project_id")],
) -> dict:
    """Tìm đoạn tài liệu liên quan trong kho của dự án đang hỏi.

    Tool TỰ thử lại với từ khoá khác nếu lần đầu không ra gì. Không cần gọi
    lại tool này với cách diễn đạt khác — đã làm rồi. Trả status "empty"
    nghĩa là kho thật sự không có, hãy nói thẳng với người dùng.

    Args:
        query: nội dung cần tìm, viết thành câu đầy đủ ý.
    """
```

- [ ] Trả `{"status": "ok", "passages": [{content, file_name, as_of_date,
      heading_path}]}` hoặc `{"status": "empty"}`. Không có `project_not_found`
      / `ambiguous`. Không trả `distance` / `score`.
- [ ] `tools/__init__.py`: `CLIENT_TOOLS = [search_docs]`, sửa `__all__`.

**Verify**
```python
print(search_docs.tool_call_schema.model_json_schema())   # CHỈ có "query"
```
rồi dựng `ToolNode(CLIENT_TOOLS)`, đưa vào state giả có `project_id` + một
`AIMessage` mang `tool_calls`, khẳng định `ToolMessage` trả về có `passages`.

## Bước 4 — State và `identify_project`

- [ ] `TAR_agent/graph_client/state.py` — viết lại, bỏ `retrieved`:

```python
class ClientState(TypedDict, total=False):
    chat_history: Annotated[list[AnyMessage], add_messages]   # sống qua các lượt
    messages: Annotated[list[AnyMessage], add_messages]       # dọn mỗi lượt
    project_id: uuid.UUID | None
    project_name: str | None
    question_raw: str
    remaining_question: str | None
    reply: str | None
    tool_call_rounds: int
    iteration_count: int
    revise_count: int
    answer: str
    verdict: str
    missing: str
    error: str | None
    outbox: list[Event]
```

- [ ] `nodes/reset.py`:

```python
def reset(state: ClientState) -> dict:
    return {
        "messages": [RemoveMessage(id=m.id) for m in state.get("messages", [])],
        "tool_call_rounds": 0, "iteration_count": 0, "revise_count": 0,
        "outbox": [], "error": None, "verdict": "", "missing": "",
        "answer": "", "reply": None, "remaining_question": None,
    }
```
      **Không** đụng `project_id`, `project_name`, `chat_history`.

- [ ] `nodes/identify_project.py` — thân node là method của `ClientGraph`
      (bước 5), file này giữ `ProjectPick` và hàm thuần:
  1. `projects = await list_projects()` — `TAR_agent/utils/projects.py`.
  2. Kho rỗng → `reply = "Kho chưa có dự án nào…"`, `project_id = None`.
  3. `self.project_identifier.ainvoke(...)` với
     `ProjectPick{ project_id: str|None; project_name: str|None;
     remaining_question: str|None; reply: str|None }`.
  4. Input prompt: danh sách `(project_id, name)`, `chat_history`, và
     `project_id` hiện có trong state.
  5. **Kiểm tra bằng code sau LLM**: `project_id` phải nằm trong danh sách vừa
     truy vấn; không thì ép `None` + log WARNING.
  6. Ghi `question_raw` = nội dung `HumanMessage` cuối của `chat_history`.
  7. Ghi `messages = [HumanMessage(question_raw)]` (nguyên văn).

- [ ] Prompt `TAR_agent/utils/prompts/client_system/identify_project.md`, bảy luật:
  1. `project_id` chỉ chọn từ danh sách cấp trong prompt. Không chắc → `None`.
  2. Chấp nhận sai chính tả và thiếu dấu ("ap truong thon" = "Ấp Trưởng Thôn").
     Không đoán bừa sang dự án khác tên.
  3. Tên dự án hay nằm giữa/cuối câu. Tách ra, phần còn lại vào
     `remaining_question`.
  4. State đã có `project_id` và tin nhắn mới không nhắc dự án nào khác → giữ
     nguyên. Chỉ đổi khi nhắc rõ một dự án khác.
  5. Lượt trước bot đã hỏi tên dự án mà người dùng trả lời một tên không có
     trong danh sách → `project_id = None`, `reply` nói rõ kho không có dự án
     tên gần giống, kèm danh sách. Cấm viết chung chung "mình không hiểu".
  6. Người dùng đổi ý / hỏi chuyện khác / gõ `/start` `/huy` → `project_id =
     None`, `reply` đáp đúng thứ họ vừa nói, không hỏi lại tên dự án.
     `/start` và `/huy` **xoá luôn dự án đang giữ** — xem Phần 2 mục 8.
  7. Câu hỏi **về bản thân kho** ("kho có những dự án nào", "bot làm được gì")
     → `project_id = None` và `reply` trả lời thẳng bằng danh sách dự án đã
     cấp ngay trong prompt này. Không hỏi ngược "bạn muốn hỏi dự án nào".

- [ ] `nodes/respond.py` — nhánh kết thúc lượt sớm: `reply` → `outbox`
      `{"kind": "answer", "data": {"text": reply}}` + append `chat_history`.

**Verify** — graph tạm `START → reset → identify_project → respond → END`,
`MemorySaver`, chạy nhiều lượt trên **một** `thread_id`:
1. "tiến độ dự án ABC thế nào" → có `project_id`, `remaining_question` không
   còn chữ "ABC".
2. "còn task C thì sao" → giữ nguyên `project_id` cũ.
3. "thế dự án XYZ" → đổi sang `project_id` mới.
4. "kho có những dự án nào" → `project_id = None`, `reply` liệt kê (luật 7).
5. `/huy` rồi "tiến độ thế nào" → `project_id = None`, `reply` hỏi dự án nào.
6. Trả lời bằng một tên không có trong kho → `reply` nói rõ không có tên đó.

## Bước 5 — agent, compose, graph (OOP)

- [ ] Xoá `TAR_agent/graph_client/nodes/retrieve.py` và `nodes/generate.py`.
- [ ] `helpers/grounding.py` — `check(answer, tool_messages, question) -> str | None`:
  - Chuẩn hoá hai phía: `1.250`/`1,250`/`1 250` → `1250`; `12,5` → `12.5`.
  - Bỏ qua: số có trong câu hỏi, ngày tháng theo mẫu, số thứ tự đầu dòng, số
    một chữ số đứng một mình.
  - Mọi tên file được dẫn phải có thật trong ToolMessage.
- [ ] Prompt `client_system/system.md` (viết lại từ `client_system.md` hiện có)
      và `client_system/compose.md`. `compose.md` chốt hợp đồng định dạng: chép
      số nguyên văn, cấm làm tròn/đổi dấu phân cách/tự tính, dẫn nguồn đúng mẫu
      `[tên_file · as_of_date]`, gặp `status: "empty"` thì trả lời đúng một câu
      là kho không có.
- [ ] `TAR_agent/graph_client/graph.py` — class, **mỗi LLM một thuộc tính có
      tên nói rõ việc nó làm**:

```python
class ClientGraph:
    def __init__(self, checkpointer: BaseCheckpointSaver) -> None:
        self.checkpointer = checkpointer
        config = load_config()
        models = config["models"]
        agent_cfg = config["agent"]

        self.tools = CLIENT_TOOLS
        self.max_tool_rounds = agent_cfg["max_tool_rounds"]
        self.max_history_messages = agent_cfg["max_history_messages"]
        self.max_iterations = agent_cfg["max_iterations"]
        self.max_revise = agent_cfg["max_revise"]

        # System prompt truyền lúc gọi chứ không nằm trong model, nên dựng sẵn được.
        self.project_identifier = create_google_genai(
            models["identify"], output_schema=ProjectPick
        )
        self.qa_agent = create_google_genai(models["client"], tools=self.tools)
        # Bản KHÔNG tool, dùng khi chạm trần max_tool_rounds — xem Phần 3 mục 7.
        self.qa_agent_final = create_google_genai(models["client"])
        self.answer_composer = create_google_genai(
            models["compose"], output_schema=Compose
        )

    # ── node ──
    async def _reset(self, state): ...
    async def _identify_project(self, state): ...
    async def _agent(self, state): ...
    async def _compose(self, state): ...
    async def _respond(self, state): ...
    # ── router ──
    def _after_identify(self, state) -> str: ...
    def _after_agent(self, state) -> str: ...
    def _after_compose(self, state) -> str: ...

    def build(self):
        builder = StateGraph(ClientState)
        builder.add_node("reset", self._reset)
        builder.add_node("identify_project", self._identify_project)
        builder.add_node("agent", self._agent)
        builder.add_node("tools", ToolNode(self.tools))
        builder.add_node("compose", self._compose)
        builder.add_node("respond", self._respond)
        builder.add_edge(START, "reset")
        builder.add_edge("reset", "identify_project")
        builder.add_conditional_edges("identify_project", self._after_identify,
                                      {"agent": "agent", "respond": "respond"})
        builder.add_conditional_edges("agent", self._after_agent,
                                      {"tools": "tools", "compose": "compose"})
        builder.add_edge("tools", "agent")
        builder.add_conditional_edges("compose", self._after_compose,
                                      {"agent": "agent", "respond": "respond"})
        builder.add_edge("respond", END)
        return builder.compile(checkpointer=self.checkpointer)


def build_client_graph(checkpointer):
    """Giữ hàm này để app/main.py gọi giống build_admin_graph."""
    return ClientGraph(checkpointer).build()
```

- [ ] `_agent`:
  - `system = load_prompt("client_system/system", TODAY=…, PROJECT=project_name,
    FOCUS=remaining_question or question_raw)`.
  - Chọn `self.qa_agent` hay `self.qa_agent_final` theo `tool_call_rounds`.
  - Cắt lịch sử `messages[-max_history_messages:]`, **lùi tiếp cho tới khi phần
    tử đầu không phải `ToolMessage`**.
  - `iteration_count += 1`; `tool_call_rounds += 1` khi reply có `tool_calls`.
  - `try/except` → `{"error": "llm_failed"}`.
- [ ] `_compose` — `Compose{ answer: str; verdict: str; missing: str }`. Sau
      LLM gọi `grounding.check(...)`; trả chuỗi thì ép `verdict = "thieu"`,
      `missing = <lý do>`.
- [ ] `_after_compose`: `"dat"`, hoặc `revise_count >= max_revise`, hoặc
      `iteration_count >= max_iterations`, hoặc mọi tool call của lượt đều
      `status == "empty"` → `"respond"`; còn lại `revise_count += 1` →
      `"agent"`.
- [ ] `_respond` — `answer` → `outbox` + append `chat_history`; `error` →
      `{"kind": "client_failed", "data": {"reason": …}}`; text rỗng →
      `reason="empty_answer"`; chạm trần → `answer` kèm câu rào.

**Verify** — script chạy graph đầy đủ với `MemorySaver` trên DB thật, một
`thread_id`, bốn lượt: hỏi dự án có thật → hỏi tiếp trong cùng dự án → hỏi thứ
không có trong kho → đổi dự án. In `outbox` và `state["messages"]` sau mỗi
lượt; khẳng định `messages` **không** tích lại `ToolMessage` của lượt trước.

## Bước 6 — Nối vào Telegram

- [ ] `app/main.py`: `app.state.client_graph = build_client_graph(checkpointer)`,
      xoá khối comment "graph_client CHƯA xây".
- [ ] `app/routers/webhooks.py`:
  - `_send(chat_id, …)` → `_send(role: BotRole, chat_id, …)`; tương tự `_tell`,
    `_tell_broken`. Sửa mọi chỗ gọi bên admin.
  - Thêm `_CLIENT_SLOTS = asyncio.Semaphore(4)`.
  - Viết `handle_client_message`: từ chối file, chặn text rỗng, rồi
    `ainvoke({"chat_history": [HumanMessage(text)]}, thread_config(CLIENT, chat_id))`,
    duyệt `outbox`. **Không** gọi `pending_interrupt`.
  - `handle_client_callback`: giữ stub, sửa docstring.
- [ ] `app/telegram/render.py`: thêm `answer` (cắt ~4000 ký tự),
      `client_failed` (`llm_failed`, `empty_answer`, `unverified`).

**Verify thủ công** (`TELEGRAM_WEBHOOK_URL` trống ⇒ polling), `python run.py`,
nhắn bot client:
- "tiến độ nhà máy A" → trả lời kèm tên file + as_of_date.
- "còn phần điện thì sao" → giữ nguyên dự án, không hỏi lại.
- "kho có những dự án nào" → liệt kê, không hỏi ngược.
- Hỏi một nội dung chắc chắn không có trong kho → nói thẳng kho không có.
- Nhắc rõ một dự án khác → đổi dự án.
- `/huy` → xoá dự án đang giữ; hỏi tiếp thì phải hỏi lại tên dự án.
- Gửi file → "Bot này chỉ trả lời câu hỏi, không nhận file."

## Bước 7 — Dọn

- [ ] Ghi "**SUPERSEDED** — xem plan mới" lên đầu `docs/plan-client.md`.
- [ ] Tạo file rỗng: `tests/test_client_graph.py`, `tests/test_client_tools.py`,
      `tests/test_search_subgraph.py`.
- [ ] Sửa docstring `TAR_agent/graph_client/__init__.py` (đang mô tả luồng
      thẳng `retrieve → generate`).
- [ ] `grep -rn "graph_admin" TAR_agent/graph_client/` phải rỗng.
- [ ] `"$PY" -m pytest tests -q` — bộ test admin phải còn xanh sau khi đổi chữ
      ký `_send` / `_tell` và sửa `config.py`.

---

# PHẦN 2 — QUYẾT ĐỊNH THIẾT KẾ CÓ ĐÁNH ĐỔI

### 1. Hai danh sách message, và cách dọn `messages`

`add_messages` là reducer **cộng dồn**. Trả `{"messages": []}` không xoá gì.
`REMOVE_ALL_MESSAGES` không tồn tại ở langgraph 0.2.60 — phải phát
`RemoveMessage(id=…)` cho từng phần tử. Bỏ sót thì `messages` tích luỹ
`ToolMessage` của mọi lượt cũ và agent trộn dữ liệu cũ với dữ liệu vừa tra.

Webhook gửi vào `chat_history`, không phải `messages`. `identify_project` là nơi
duy nhất ghi `HumanMessage` vào `messages`; `respond` là nơi duy nhất ghi câu
trả lời vào `chat_history`.

Đánh đổi: `chat_history` chưa cắt bớt, phình theo số lượt — phải theo dõi trước
khi thêm summary middleware.

### 2. Giữ cả `question_raw` lẫn `remaining_question`

`messages` nhận nguyên văn chữ người dùng gõ; `remaining_question` chỉ vào
system prompt của agent như gợi ý trọng tâm. Mã số và tên riêng tới được BM25
đúng như người dùng gõ.

Đánh đổi: agent thấy tên dự án hai lần (trong câu gốc và trong biến `PROJECT`).
Đổi lại không có đường nào cho một lần viết lại sai âm thầm làm hỏng truy vấn.

### 3. `identify_project` gọi LLM ở mọi lượt

Một nguồn duy nhất, không nhánh tắt. Bù bằng khối model `identify`
(`gemini-2.5-flash-lite`, temperature 0).

Đánh đổi: +1 lượt LLM (~1–2s) cho cả những tin nhắn hiển nhiên như "cảm ơn". Và
model rẻ hơn thì luật 4 ("giữ nguyên dự án trừ khi nhắc rõ dự án khác") là thứ
dễ sai nhất — chỗ phải soi kỹ khi verify bước 4.

### 4. Câu hỏi ngoài phạm vi dự án — xử bằng prompt

Luật 7. `identify_project` đã cầm sẵn danh sách dự án trong prompt, nên nó tự
trả lời "kho có gì" mà không tốn thêm lời gọi nào, không thêm trường vào
`ProjectPick`, không thêm node.

Đánh đổi: một node giờ làm bốn việc (chọn dự án, tách câu hỏi, hỏi lại, trả lời
câu hỏi về kho) và prompt dài thêm. Vẫn giữ được bảo đảm mạnh nhất của thiết
kế: `project_id` **không bao giờ** là `None` khi vào `agent`, nên
`InjectedState` luôn có giá trị thật.

Rủi ro: luật 7 và luật 5 giẫm chân nhau — "dự án ABC là gì" có thể bị hiểu là
câu hỏi về kho thay vì câu hỏi trong dự án ABC. Prompt phải nêu ví dụ đối lập
cho cả hai.

### 5. BM25 trong RAM, kho trong Postgres

Cache theo `project_id`, vô hiệu bằng `(count, max(uploaded_at))`.

Cái giá: mỗi lần tra thêm 1 câu COUNT; cache lạnh của dự án vài nghìn chunk tốn
một lượt SELECT toàn bộ nội dung + tokenize; nhiều worker uvicorn thì mỗi worker
giữ một bản riêng. Vượt ~50k chunk/dự án thì chuyển sang Postgres FTS — thiết
kế đó nằm sẵn ở `docs/plan-client.md` bước 1–2.

### 6. `grade` chấm cả lô trong một lượt

Rẻ và bắt được ngữ cảnh chéo giữa các đoạn, nhưng một đoạn tốt lẫn giữa bốn
đoạn rác dễ bị chấm chung là "không đủ".

### 7. `compose` tự chấm bản nó vừa viết

Model vừa viết xong rồi tự hỏi "bản này đúng không" — nó sẽ nói đúng. `verdict`
một mình không đáng tin; `grounding.check` mới là lớp cưỡng chế. Regex chặn bịa
**số**, không chặn bịa **ý**: "doanh thu 1250" và "doanh thu *giảm* 1250" đều
qua được.

### 8. Một user chỉ có một `chat_id` ⇒ `project_id` dính vĩnh viễn

Trên Telegram không có "chat mới": `thread_id = "CLIENT:<chat_id>"` là cố định
cho mỗi người, và checkpointer giữ `project_id` mãi mãi. Nghĩa là **không có
đường tự nhiên nào để về trạng thái sạch** — người dùng hỏi dự án A hôm nay,
tháng sau mở lại vẫn đang ở dự án A và hỏi cộc lốc sẽ được trả lời bằng tài
liệu dự án A mà không ai nhắc gì.

Hai lớp giảm nhẹ, cả hai đều phải có:
- `/start` và `/huy` xoá `project_id` (luật 6 trong prompt + `identify_project`
  ghi `project_id=None, project_name=None` vào state).
- Mỗi câu trả lời **nêu rõ tên dự án** đang trả lời — đưa vào
  `client_system/compose.md`. Đây là thứ khiến "trả lời nhầm dự án" nhìn ra
  được ngay thay vì trông y hệt trả lời đúng.

Chưa làm: hết hạn theo thời gian (ví dụ quá 24h không nhắn thì quên dự án). Cần
mốc thời gian trong state, để sau.

### 9. Chỉ còn một tool — vòng ReAct gần như thoái hoá

`project_id` do graph quyết, `search_docs` tự thử lại bên trong. Agent chỉ còn
hai lựa chọn: gọi `search_docs` với một `query`, hoặc thôi. Vòng
`agent ⇄ tools` gần như luôn chạy đúng một lần.

Vẫn giữ vì nó là chỗ tra nhiều lượt với từ khoá khác nhau cho câu hỏi nhiều ý,
và là chỗ `query_project_db` / `calc_ratio` cắm vào sau mà không phải dựng lại
graph. Nếu sau vài tuần vẫn chỉ một tool thì cân nhắc bỏ vòng, gọi thẳng
`search_docs` rồi vào `compose`.

### 10. Vòng `compose → agent` khi kho rỗng

Ép `thieu` rồi đá về agent là vô ích nếu kho thật sự không có dữ liệu — chỉ đốt
thêm 2 vòng để ra đúng câu trả lời cũ. Đã chặn: mọi tool call của lượt đều
`status == "empty"` thì `_after_compose` đi thẳng `respond`.

---

# PHẦN 3 — CẠM BẪY PHÁT HIỆN KHI ĐỌC REPO

### Về xác định dự án — chỗ phải xoá hoặc sửa

Tin tốt: **không có đường resolve nào đang chạy thật cần xoá.**
`TAR_agent/utils/projects.py:resolve_project` **không có nơi gọi nào** (grep
toàn repo), và `persistence/proc/projects.py:find_projects` chỉ được gọi bởi
chính nó. Cả nhánh khớp lỏng đang là code chết.

- `TAR_agent/utils/projects.py:resolve_project` + `ProjectMatch` → xoá, hoặc để
  lại kèm ghi chú "không dùng ở luồng client, xem nodes/identify_project.py".
  Kéo theo `proc.find_projects` và `text.ilike_pattern` / `LIKE_ESCAPE` thành
  mồ côi (`ilike_pattern` còn test riêng ở `tests/test_text.py`).
- `TAR_agent/utils/projects.py:list_projects` → **giữ**, `identify_project` dùng
  đúng hàm này. Không cần thêm `latest_as_of`.
- `TAR_agent/graph_client/tools/search.py` → xoá cả hai tool cũ. Docstring đầu
  file đang dặn "gọi `list_projects` trước", sai hẳn với thiết kế mới.
- `TAR_agent/graph_client/nodes/retrieve.py` → xoá file. Docstring ghi
  `project = resolve_project(...)`.
- `TAR_agent/graph_admin/nodes/ask_project.py` → **không đụng**. Đó là admin
  chọn dự án bằng bàn phím inline + `interrupt()`, không phải khớp tên.
- `TAR_agent/graph_admin/nodes/handle_text.py` (`create_project`,
  `list_projects`) → **không đụng**.

### Checkpointer và `thread_id` — đã đúng thứ thiết kế cần

`app/main.py` lifespan: `AsyncPostgresSaver` trên `AsyncConnectionPool` dùng
chung cho cả hai graph. `app/routers/webhooks.py:thread_config` →
`{"configurable": {"thread_id": f"{role.name}:{chat_id}"}}`, đã tách theo vai.
`app.state.client_graph = None` là móc duy nhất cần thay. Mỗi chat đã có
`asyncio.Lock` chặn hai tin nhắn cùng lúc đè lên một `thread_id`.

Hệ quả của việc `thread_id` cố định theo người: xem Phần 2 mục 8.

### Node `reset` hiện tại — **không** có ở graph_client

`graph_client` chưa có node reset nào. Cái đang chạy là
`TAR_agent/graph_admin/nodes/route.py`: nó dọn state bằng cách trả `None`/`0`
cho từng khoá, và **không đụng `messages`** (graph_admin không có `messages`).
Khuôn đó không dùng lại được cho việc dọn `messages` — xem Phần 2 mục 1.

Lý do phải dọn thì `route.py` đã ghi sẵn: khoá ngoài `messages` không có
reducer, để nguyên thì một chat từng dính `error` mang lỗi cũ sang mọi lượt sau.

### Các bẫy còn lại

1. **`REMOVE_ALL_MESSAGES` không có ở langgraph 0.2.60.** Đã kiểm tra bằng
   import. Phải `RemoveMessage(id=…)` từng phần tử.
2. **`_send` / `_tell` / `_tell_broken` chốt cứng `ADMIN.bot`.** Đúng thứ
   `app/telegram/sender.py` cảnh báo: "lấy nhầm là câu trả lời của client đi ra
   từ bot admin". Phải thêm tham số `role` trước khi nối bot client.
3. **`config.py` hiện chưa có `create_google_genai` / `load_config`** — nó đang
   là `_chat_model` (private) + `admin_model` / `client_model` +  các hằng số
   rời `MODELS`/`AGENT`/`EMBEDDING`/`CHUNKING`. Bước 0 bổ sung, giữ hàm cũ làm
   vỏ mỏng để không phá `graph_admin`.
4. **Nhầm interpreter.** Python trên PATH mặc định là env base với
   **langgraph 1.2.10 / langchain-core 1.5.2** — thế hệ khác hẳn, không tương
   thích. Env thật là `tar_env` (langgraph 0.2.60, langchain 0.3.30,
   langchain-core 0.3.86). `conda run -n tar_env` **đang hỏng** trên máy này
   (conda 26.5.3 ném lỗi plugin) — gọi thẳng
   `D:/QUYEN/DI_LAM/miniconda3/envs/tar_env/python.exe`.
5. **`langchain` và `rank_bm25` chưa khai trong `requirements.txt`.**
   `langchain 0.3.30` tình cờ có sẵn trong `tar_env` nên máy bạn chạy được;
   Docker build lại sẽ nổ `ImportError` ở `EnsembleRetriever`. `rank_bm25`
   **chưa cài ở đâu cả** — đã kiểm tra, MISSING. `rapidfuzz 3.14.5` có sẵn
   nhưng thiết kế mới không dùng.
6. **`langgraph-prebuilt 1.1.0` cài chung với langgraph 0.2.60.** Hai thế hệ
   nằm cạnh nhau. `from langgraph.prebuilt import InjectedState, ToolNode` hiện
   phân giải về bản 0.2.x (đã xác nhận qua `inspect.getsource`) — nếu sau này
   import lỗi lạ ở `ToolNode`, đây là chỗ soi đầu tiên.
7. **`InjectedState("project_id")` có thật ở 0.2.60** — đã đọc source. `ToolNode`
   mặc định `messages_key="messages"`, khớp danh sách ReAct của ta (không phải
   `chat_history`).
8. **Gemini trả 400 ở lượt SAU** nếu lịch sử còn `tool_calls` treo mà thiếu
   `ToolMessage` trả lời. Vì thế chạm trần thì đổi sang `qa_agent_final` (không
   bind tool) chứ không cắt cụt vòng lặp, và cắt lịch sử phải lùi qua
   `ToolMessage` đứng đầu. Lỗi này không nổ tại chỗ gây ra nên rất khó lần.
9. **Cột `metadata` của `doc_chunk` mang tên Python là `chunk_metadata`** —
   `metadata` là tên dành riêng của SQLAlchemy.
10. **`chunks.search` hiện `select(DocChunk)`** nên kéo cả cột `embedding` (768
    float/dòng) về rồi bỏ đi. `load_corpus` mới **không** được lặp lại lỗi đó —
    nó đọc cả bảng của dự án chứ không phải 5 dòng.
11. **`load_prompt` dùng `{{TÊN}}`, không phải `str.format`** — và
    `_PROMPT_DIR / f"{name}.md"` chấp nhận `name` có dấu `/`, nên
    `load_prompt("client_system/compose")` chạy đúng trên cả Windows lẫn Linux.
12. **`nodes/__init__.py` xuất lại HÀM trùng tên với MODULE.** Viết
    `from TAR_agent.graph_client.nodes import agent` trả về *hàm*, không phải
    module — `monkeypatch.setattr` sẽ nổ `AttributeError`. Cần
    `importlib.import_module` khi viết test sau này.
13. **`unstructured[xlsx]` vẫn đang comment trong `requirements.txt`.** Không
    liên quan luồng client, nhưng nạp `.xlsx` bằng bot admin để dựng dữ liệu
    verify thì cần nó.
