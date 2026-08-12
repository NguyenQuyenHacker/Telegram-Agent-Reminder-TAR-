# Plan: Mục 1, 2, 4 trong todo.md

## Context

Sau lần này, TAR chỉ còn **hai luồng**: Job A (scheduler quét việc tới hạn, không AI) và Job C (LangGraph agent, chỗ duy nhất gọi LLM). Toàn bộ tương tác của người dùng đi qua tin nhắn với agent — **không còn nút inline nào**.

Bốn thay đổi kiến trúc đã chốt:

| | |
|---|---|
| **Xóa Job B** | Gộp tin nhắc rồi thì nút không control được. Xóa `keyboards.py`, `callback_service.py`, nhánh callback ở webhook/polling. Báo xong / hủy / đổi hạn / hoàn tác đều bằng chat. |
| **Bỏ `snoozed`** | `TaskStatus` còn `pending` / `done` / `cancelled`. Bảng 4 nhịp nhắc rút còn **một biến `PENDING_INTERVAL_MIN`**. |
| **Thêm `cancelled`** | Không xóa dòng khỏi DB: hủy nhầm khôi phục được bằng 1 câu chat, và đồng bộ lại báo cáo cũ không hồi sinh việc đã hủy. |
| **Mã task `TB-002`** | Người dùng phải gõ được mã. `task_id` sha256 **giữ nguyên làm khóa chính** (nó là căn cước idempotent của R7 — graph.py:91); thêm cột `code` riêng cho người đọc. |

Ba khoảng trống cần lấp — mục 3 (ảnh/thoại) để lần sau:

- **Mục 1 — Không sửa được task bằng chat.** Agent hiện chỉ đọc: 2 tool `query_tasks` / `search_reports`.
- **Mục 2 — Không hiểu hạn tương đối.** "2 ngày nữa" không ra ngày; `missing_due_date_prompt` hỏi hạn nhưng câu trả lời rơi vào nhánh hỏi đáp và bị bỏ rơi. → làm bằng prompt, không viết parser Python.
- **Mục 4 — Nhắc rời rạc.** `run_job_a` gửi 1 tin/task.

---

## Luồng node của graph: trước và sau

Câu hỏi "thêm tool thì luồng có đổi không" — **nhánh báo cáo không đổi một dòng nào; nhánh hỏi đáp mọc thêm một cái đuôi 3 node**. Lý do: tool mới chỉ *đề xuất*, mà đề xuất thì phải có chốt duyệt, và chốt duyệt là `interrupt()` — không nhét vào node `tools` được.

### Hiện tại

```
START ──route_entry──┬── report ──▶ extract_tasks ──▶ ask_confirm ──▶ read_decision ──┬──▶ save_to_db ──▶ END
                     │                    ▲               ▲   (interrupt)             │
                     │                    └── edit ───────┴── unclear ────────────────┤
                     │                                                        abandoned──▶ END
                     │
                     └── qa ──────▶ agent ◀──▶ tools
                                      └──────────────▶ answer ──▶ END
```

### Sau khi làm

```
START ──route_entry──┬── report ──▶ (GIỮ NGUYÊN, không sửa gì)
                     │
                     └── qa ──────▶ agent ◀──▶ tools
                                      └────▶ answer ──route_after_answer──┬── END
                                                                          │
                                        ┌─────────────────────────────────┘
                                        ▼
                              ask_update_confirm ──▶ read_update_decision ──route_after_update──┬──▶ apply_updates ──▶ END
                                  (interrupt)                                                   └──▶ END
```

### Bảng node

| Node | Tình trạng | Ghi chú |
|---|---|---|
| `extract_tasks`, `ask_confirm`, `read_decision`, `save_to_db` | **giữ nguyên** | `save_to_db` chỉ thêm 1 dòng ghi `awaiting_due_task_ids` (GĐ 3) |
| `agent` (`call_model`) | **giữ nguyên** | GĐ 3 thêm 1 dòng ngữ cảnh vào system prompt |
| `tools` (`call_tools`) | **sửa nhẹ** | vẫn vòng lặp cũ, `max_tool_rounds=3` không đổi; thêm: kết quả nào có `status == "proposed"` thì gom vào `pending_updates` |
| `answer` | **sửa nhẹ** | vẫn gửi text của LLM; thêm: có `pending_updates` thì gửi kèm bảng đề xuất |
| `route_agent` | **giữ nguyên** | |
| `route_after_answer` | **mới** | có `pending_updates` → `ask_update_confirm`, không thì `END` |
| `ask_update_confirm` | **mới** | chỉ `interrupt()`, rỗng hoàn toàn |
| `read_update_decision` | **mới** | dùng lại `parse_free_text_decision` sẵn có |
| `apply_updates` | **mới** | chỗ **duy nhất** trong nhánh hỏi đáp ghi DB |

**Vì sao `answer` phải gửi bảng đề xuất chứ không phải `ask_update_confirm`:** đúng bài học đã ghi ở graph.py:136-150 — node có `interrupt()` chạy lại từ đầu mỗi lần resume, đặt `send_message` trong đó thì user nhận lại bảng cũ mỗi lần họ trả lời. Cùng lý do `extract_tasks` gửi bảng chứ không phải `ask_confirm`.

**Điểm dễ sót ở tầng webhook:** [app/routers/webhooks.py](../app/routers/webhooks.py) `_is_awaiting_confirm` (dòng 22) hiện chỉ nhận `"ask_confirm"`. Có 2 node interrupt rồi thì phải kiểm tra cả tập `{"ask_confirm", "ask_update_confirm"}` — không sửa thì câu "ok" bị `classify_message` coi là câu hỏi mới và đề xuất treo mãi.

---

## Giai đoạn 0 — Dọn nền

Giai đoạn này **xóa nhiều hơn thêm**.

### 0.1 Xóa Job B

- Xóa [app/telegram/keyboards.py](../app/telegram/keyboards.py) và [app/services/callback_service.py](../app/services/callback_service.py).
- [app/routers/webhooks.py](../app/routers/webhooks.py) dòng 78-83: bỏ nhánh `update.callback_query` và import `handle_task_callback`.
- [app/telegram/polling.py](../app/telegram/polling.py): bỏ `@dp.callback_query()`.
- [app/services/reminder_service.py](../app/services/reminder_service.py) dòng 44-49: bỏ `reminder_keyboard(...)`.
- [app/telegram/sender.py](../app/telegram/sender.py): giữ tham số `reply_markup` (mặc định `None`) — không cần sửa.
- [app/telegram/messages.py](../app/telegram/messages.py): xóa `snooze_confirmation_text`, `undo_expired_text`. `done_confirmation_text` / `undo_confirmation_text` giữ lại, GĐ 1 dùng.

### 0.2 Bỏ `snoozed`, rút nhịp nhắc còn một biến

- [persistence/models/task.py](../persistence/models/task.py): bỏ `snoozed` khỏi `TaskStatus`, thêm `cancelled = "cancelled"` và cột `cancelled_at: datetime | None = None`.
- [app/core/config.py](../app/core/config.py) dòng 20-23: bốn biến interval → **một** `PENDING_INTERVAL_MIN` (mặc định 30). Cập nhật `.env`, `.env.example`, README.
- [app/core/priority.py](../app/core/priority.py):
  - `interval_for(priority, status)` (dòng 20) → xóa hẳn; nơi nào cần nhịp thì đọc thẳng `settings.pending_interval_min`. Cột `remind_interval_min` cũng bị bỏ (xem 0.4), nên hai chỗ gọi ở graph.py:207 và reminder_service.py:60 chỉ còn bỏ tham số đi.
  - `compute_next_remind_at(priority, status, ref)` (dòng 63) → `compute_next_remind_at(ref)`.
  - `maybe_escalate` (dòng 29) **giữ nguyên** — priority giờ quyết định task nằm rổ nào trong tin gộp (GĐ 2), chỉ không còn ảnh hưởng nhịp nhắc.
- [persistence/proc/tasks.py](../persistence/proc/tasks.py): xóa `snooze_task` (dòng 104).

### 0.3 Mã task `TB-002`

**Sinh tiền tố không cần từ điển:** lấy chữ cái đầu của **mọi** từ trong tên nhóm rồi giữ **2 chữ cuối**. Không có danh sách stopword nào phải bảo trì.

```
"App Trưởng thôn, trưởng bản"  ->  A T T T B  ->  "TB"
"Hệ thống báo cáo"             ->  H T B C    ->  "BC"
"Kho"                          ->  K          ->  "KH"   (1 từ: lấy 2 chữ đầu của từ đó)
```

- File mới `app/core/task_code.py` — thuần, không I/O, không hằng số nghiệp vụ:
  ```python
  def derive_prefix(group: str) -> str   # bỏ dấu (unicodedata NFD), lấy initials, giữ 2 chữ cuối
  def format_code(prefix: str, seq: int) -> str      # f"{prefix}-{seq:03d}"
  def parse_code(text: str) -> str | None            # "tb2" / "TB-2" / "#TB-002" -> "TB-002"
  ```
- DAO mới `persistence/proc/group_codes.py` → `allocate_code(group) -> str`: trong một transaction, `SELECT … FOR UPDATE` dòng `group_code` của nhóm; chưa có thì `INSERT` với `derive_prefix(group)`, **đụng prefix thì thêm hậu tố số** (`TB` bận → `TB2`); lấy `next_seq`, `+1`, commit.
- [persistence/proc/tasks.py](../persistence/proc/tasks.py) `upsert_task` (dòng 28): chỉ cấp `code` khi **tạo mới**; cập nhật dòng cũ thì giữ nguyên mã, kể cả khi tên nhóm đổi — mã đã in ra cho người dùng rồi.

### 0.4 Gộp schema về một file duy nhất

Bỏ lối chia file theo bảng (`01_task.sql`, `02_report.sql`) và **không dùng file migration tăng dần**. Thay bằng **một** `persistence/schema/schema.sql` mô tả trạng thái cuối — ai clone về chỉ chạy đúng một lệnh:

```sql
-- persistence/schema/schema.sql
-- Toàn bộ schema của TAR. Chạy: psql "$DATABASE_URL" -f persistence/schema/schema.sql

CREATE TABLE IF NOT EXISTS task (
    task_id          TEXT PRIMARY KEY,
    code             TEXT UNIQUE,
    "group"          TEXT NOT NULL,
    content          TEXT NOT NULL,
    due_date         DATE,
    priority         TEXT NOT NULL CHECK (priority IN ('urgent', 'normal')),
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'done', 'cancelled')),
    next_remind_at   TIMESTAMPTZ NOT NULL,
    last_reminded_at TIMESTAMPTZ,
    done_at          TIMESTAMPTZ,
    cancelled_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_due_scan ON task (status, next_remind_at);

-- Bộ đếm mã việc theo nhóm: "App Trưởng thôn, trưởng bản" -> TB-001, TB-002, ...
CREATE TABLE IF NOT EXISTS group_code (
    "group"  TEXT PRIMARY KEY,
    prefix   TEXT NOT NULL UNIQUE,
    next_seq INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS report (
    report_id   TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    "group"     TEXT,
    raw_text    TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_report_received_at ON report (received_at);
```

Khác biệt so với schema cũ, ngoài `code` / `cancelled_at` / bảng `group_code`:

- `status` bỏ `'snoozed'`, thêm `'cancelled'`.
- **Bỏ cột `remind_interval_min`.** Nhịp nhắc giờ là một hằng số cấu hình (`PENDING_INTERVAL_MIN`) nên cột này luôn mang cùng một giá trị — giữ lại chỉ gây hiểu nhầm là mỗi task có nhịp riêng. Kéo theo: bỏ field khỏi [persistence/models/task.py](../persistence/models/task.py), bỏ tham số khỏi `upsert_task` (dòng 28) và `reschedule_task` (dòng 119), sửa chỗ gọi ở graph.py:207 và reminder_service.py:60.

**DB đang chạy:** file trên toàn `IF NOT EXISTS` nên chạy lại trên DB cũ sẽ **không** cập nhật bảng `task` sẵn có. Dữ liệu hiện tại chỉ là báo cáo test ⇒ cách gọn nhất là `DROP TABLE task, report;` rồi chạy `schema.sql`. Ghi hai dòng này vào README như ghi chú "nếu đang có DB từ bản cũ", **không** tạo file migration.

### 0.5 DAO mới trong [persistence/proc/tasks.py](../persistence/proc/tasks.py)

```python
def cancel_task(task_id) -> Task | None
def undo_cancel(task_id) -> Task | None                      # đối xứng undo_done, trong 24h
def set_due_date(task_id, new_due, next_remind_at) -> Task | None
def find_tasks_by_reference(ref: str, limit: int = 5) -> list[Task]
```

- `set_due_date` nhận sẵn `next_remind_at` do tầng gọi tính (giống chữ ký `reschedule_task` dòng 119) — DAO không import `app.core`.
- `find_tasks_by_reference`: `parse_code(ref)` ra kết quả → tra cột `code`; ngược lại → `ILIKE %ref%` trên `content` **và** `group`. Luôn giới hạn `status = pending`.
- `get_due_tasks` (dòng 13): `status != done` → `status == pending`. Đây là chỗ thỏa **"task đã xong hoặc đã hủy thì không nhắc nữa"**.
- `upsert_task` (dòng 41): guard `existing.status == done` → `in (done, cancelled)`.
- `query_tasks` (dòng 143) và tool `query_tasks`: thêm `code` vào kết quả.
- Tách 24h đang hardcode ở `undo_done` (dòng 91) thành `settings.undo_window_hours`, dùng chung với `undo_cancel`.

---

## Giai đoạn 1 — Mục 1: cập nhật task bằng chat

### 1.1 Tool mới (chỉ đề xuất, không ghi)

File mới `reminder_agent/utils/tools/update_task.py`, đăng ký vào `ALL_TOOLS` ở [reminder_agent/utils/tools/__init__.py](../reminder_agent/utils/tools/__init__.py).

```python
@tool
async def propose_task_update(
    task_ref: str,
    action: Literal["done", "cancel", "reschedule"],
    new_due_date: str | None = None,
) -> dict:
    """Đề xuất cập nhật một đầu việc. KHÔNG ghi dữ liệu — người dùng sẽ xác nhận bằng tin nhắn.

    Args:
        task_ref: mã việc (ví dụ "TB-002") hoặc một phần nội dung/tên nhóm.
        action: "done" khi người dùng báo đã xong, "cancel" khi bỏ việc,
            "reschedule" khi đổi hạn.
        new_due_date: hạn mới dạng "YYYY-MM-DD", bắt buộc khi action="reschedule".
    """
```

Bọc DB trong `asyncio.to_thread` như `query_tasks` đang làm:

| `find_tasks_by_reference` | Tool trả về | LLM làm gì |
|---|---|---|
| 0 kết quả | `{"status": "not_found"}` | báo không tìm thấy |
| ≥2 kết quả | `{"status": "ambiguous", "candidates": [...]}` | đọc mã từng ứng viên, hỏi user chọn |
| 1 kết quả | `{"status": "proposed", "task": {...}, ...}` | xem 1.2 |

`action="reschedule"` mà `new_due_date` thiếu / không ISO → `{"status": "invalid_due_date"}` để LLM hỏi lại, **không đoán**.

### 1.2 Đuôi duyệt-bằng-chat của nhánh hỏi đáp

(sơ đồ ở phần "Luồng node" đầu tài liệu)

- [reminder_agent/utils/state.py](../reminder_agent/utils/state.py): thêm `pending_updates: list[dict]`, `update_reply: str | None`, `update_status: Literal["approved","abandoned","unclear"] | None`.
- `call_tools` (dòng 268): kết quả nào là dict có `status == "proposed"` thì gom vào `pending_updates`.
- `answer` (dòng 298) — gửi text của LLM, rồi nếu có `pending_updates` thì gửi `update_confirm_text(...)`:
  ```
  Tôi sẽ cập nhật:
  • TB-002 Viết tài liệu mô tả → ✅ đã xong
  • TB-005 Quay demo → 🗑️ hủy
  • QT-007 Trình sếp duyệt → 📅 hạn mới 19/07/2026 (Thứ Bảy)

  Nhắn "ok" để tôi ghi, hoặc "thôi" để bỏ qua.
  ```
- `ask_update_confirm` — **chỉ `interrupt()`**, trả `{"update_reply": reply}`.
- `read_update_decision` — dùng lại `parse_free_text_decision` ([reminder_agent/utils/intent.py](../reminder_agent/utils/intent.py) + `decision_system.md` sẵn có); coi `edit` như `unclear` (đề xuất chỉ có ok/không, muốn đổi thì nhắn lại từ đầu).
- `apply_updates` — ghi DB, mỗi thay đổi nhắn lại một dòng (**"nhắn xác nhận lại cho user sau mỗi thay đổi"**):
  - `done` → `mark_done` → `done_confirmation_text`
  - `cancel` → `cancel_task` → `cancel_confirmation_text`
  - `reschedule` → `set_due_date(task_id, new_due, compute_next_remind_at(now_local()))` → `due_updated_text` ⇒ **lịch nhắc chạy theo hạn mới**
- Reset đầu mỗi lượt: thêm `"pending_updates": []` cạnh `"tool_call_rounds": 0` (webhooks.py dòng 48 và 60) — cùng lý do đã ghi ở comment dòng 53-54.

### 1.3 Prompt

[reminder_agent/prompts/agent_system.md](../reminder_agent/prompts/agent_system.md) — thêm gọn:

- Có 3 tool; `propose_task_update` **không ghi dữ liệu**, chỉ đề xuất — đừng nói "đã xong rồi" khi user chưa xác nhận.
- Không rõ user nói việc nào thì `query_tasks` trước rồi hỏi lại, không đoán.
- Mọi task đều có `code` dạng `TB-002` — luôn nhắc tới việc bằng mã đó.

---

## Giai đoạn 2 — Mục 4: gộp tin nhắc thành một

Độc lập với GĐ 1, làm song song được. Không đụng graph.

### 2.1 Gom nhóm + dựng tin

- File mới `app/core/reminder_digest.py`: `group_by_bucket(tasks, now) -> dict[str, list[Task]]`, 3 rổ theo thứ tự hiển thị: `overdue` (`due_date < today`) → `urgent` (`maybe_escalate(...) == urgent`, chưa quá hạn) → `normal` (còn lại, gồm task chưa có hạn). Hàm thuần, không I/O ⇒ test được.
- [app/telegram/messages.py](../app/telegram/messages.py): `digest_text(buckets, now)` — HTML, dùng lại `_display_content` và `_due_line`:
  ```
  🔔 Bạn có 5 việc cần làm

  ⛔ QUÁ HẠN (2)
  TB-002 · Viết tài liệu mô tả — 📅 Hạn 20/07 · ⏰ Quá hạn 3 ngày
  TB-005 · Quay demo — 📅 Hạn 18/07 · ⏰ Quá hạn 5 ngày

  🔴 ƯU TIÊN (1)
  QT-001 · Bổ sung quy trình tin học hóa — 📅 Hạn 19/07 · ⏳ Còn 2 ngày

  🔵 BÌNH THƯỜNG (2)
  ...
  ```

### 2.2 Sửa `run_job_a`

[app/services/reminder_service.py](../app/services/reminder_service.py) dòng 64-82:

```python
now = now_local()
due_tasks = await asyncio.to_thread(get_due_tasks, now)
if not due_tasks:
    return
if not is_within_send_window(now):
    for task in due_tasks:
        await _defer_until_next_window(task, now)     # giữ nguyên, R6
    return

buckets = group_by_bucket(due_tasks, now)
try:
    await send_message(settings.telegram_chat_id, digest_text(buckets, now),
                       parse_mode=REMINDER_PARSE_MODE)
except Exception:
    log.exception("Gửi tin gộp thất bại, để lượt sau thử lại")
    return                                    # KHÔNG đụng next_remind_at
for task in due_tasks:
    await asyncio.to_thread(reschedule_task, task.task_id,
                            compute_next_remind_at(now), now, maybe_escalate(task, now))
```

`_send_and_reschedule` (dòng 32) thành `_reschedule_after_digest`. Bất biến ở dòng 50-52 — **gửi lỗi thì không dời mốc, lượt sau gặp lại** — giữ nguyên, giờ áp cho cả lô.

### 2.3 Sửa docs

- [todo.md](todo.md) mục 4: bỏ gạch đầu dòng "Có nút thao tác nhanh cho từng task".
- [luong-xu-ly-agent.md](luong-xu-ly-agent.md) và [bai-noi-gioi-thieu.md](bai-noi-gioi-thieu.md) mô tả Job B khá kỹ (bảng 4 nhịp nhắc, nút Done/Snooze/Undo, §3, bảng "Jobs A và B không dùng LLM"). Viết lại theo kiến trúc mới: **hai luồng, mọi tương tác qua tin nhắn với agent**, kèm sơ đồ node mới. Không sửa thì bài nói đang mô tả một hệ thống không còn tồn tại.

---

## Giai đoạn 3 — Mục 2: hạn tương đối (bằng prompt)

### 3.1 Luật ngày dùng chung

File mới `reminder_agent/prompts/_date_rules.md`, `loader.load_prompt` ([reminder_agent/prompts/loader.py](../reminder_agent/prompts/loader.py)) nối vào cả `extract_system` lẫn `agent_system`:

```
Hôm nay {{TODAY}} (giờ VN). Mọi cách nói hạn tương đối — "2 ngày nữa",
"1 tuần nữa", "1 tháng nữa", "cuối tuần này", "cuối tháng này" — phải cộng vào
{{TODAY}} rồi xuất dạng "YYYY-MM-DD". Cộng tháng: cùng ngày ở tháng sau, ngày đó
không tồn tại thì lấy ngày cuối tháng. Không suy ra được thì để null, không bịa.
```

Cả hai prompt đã nhận `{{TODAY}}` sẵn (graph.py:109 và 242-246) ⇒ không phải sửa chỗ gọi. Mốc là ngày user gửi tin theo `now_local()` = `Asia/Ho_Chi_Minh` ([app/core/datetime_utils.py](../app/core/datetime_utils.py)).

### 3.2 Trả lời bổ sung hạn cho task đang thiếu

Hiện `save_to_db` (dòng 210-211) gửi `missing_due_date_prompt` rồi **bỏ rơi** câu trả lời: LLM không biết "2 ngày nữa" nói về việc nào.

- [reminder_agent/utils/state.py](../reminder_agent/utils/state.py): thêm `awaiting_due_task_ids: list[str]`.
- `save_to_db` (dòng 190): gom `task_id` của mọi task `due_date is None` vào field đó.
- `call_model` (dòng 237): field không rỗng thì đọc lại các task đó, **lọc bỏ task đã có hạn** (danh sách tự cạn, không cần ai dọn), nối một dòng ngữ cảnh vào system prompt:
  > "Bạn vừa hỏi hạn cho: TB-002 <nội dung>, … — câu trả lời tiếp theo nhiều khả năng là hạn cho chúng."
- LLM tự gọi `propose_task_update(task_ref="TB-002", action="reschedule", new_due_date=…)`; bước xác nhận ở 1.2 chính là **"nhắn lại ngày deadline vừa tính cho user xác nhận"**.

---

## Cấu hình

[app/core/config.py](../app/core/config.py) + `.env` + `.env.example`:

| Biến | | |
|---|---|---|
| `URGENT_PENDING_INTERVAL_MIN`, `URGENT_SNOOZED_INTERVAL_MIN`, `NORMAL_PENDING_INTERVAL_MIN`, `NORMAL_SNOOZED_INTERVAL_MIN` | **xóa** | gộp thành 1 |
| `PENDING_INTERVAL_MIN` | mới, 30 | nhịp nhắc duy nhất |
| `UNDO_WINDOW_HOURS` | mới, 24 | tách khỏi hardcode ở tasks.py:91 |

---

## Thứ tự thực thi

```
GĐ 0 (xóa Job B + bỏ snoozed + mã TB-002 + DAO)   ← chặn tất cả
   ├─ GĐ 1 (mục 1: tool + đuôi duyệt bằng chat)
   │     └─ GĐ 3 (mục 2: luật ngày + bổ sung hạn)   ← cần tool của GĐ 1
   └─ GĐ 2 (mục 4: gộp tin nhắc)                    ← song song được với GĐ 1
```

Dừng sau bất kỳ giai đoạn nào hệ thống vẫn chạy được.

## Việc cố ý KHÔNG làm

- **Mục 3 (ảnh + tin nhắn thoại)** — sẽ tìm hiểu giải pháp và test sau.
- **Mục 5 (task con)** — ngoài phạm vi. Lưu ý cho sau: cần `parent_task_id` trên bảng `task`, `get_due_tasks` phải quyết định nhắc cha hay con, mã con dạng `TB-002.1`. Nếu chắc sẽ làm, thêm luôn `parent_task_id TEXT REFERENCES task(task_id)` vào `schema.sql` ngay từ giờ để khỏi dựng lại bảng lần nữa.
- **Parser ngày bằng Python** — đã chốt để LLM tính qua prompt. Đánh đổi: model có thể lệch ở mẫu lạ; bước xác nhận ở GĐ 1 là lưới an toàn (user thấy ngày cụ thể trước khi ghi).
- **Nhiều người dùng** — vẫn gửi về một `TELEGRAM_CHAT_ID`.
- **Nút inline** — bỏ hoàn toàn, kể cả "Hoàn tác". Hoàn tác giờ là câu chat ("khôi phục TB-005"), vẫn giới hạn 24h qua `undo_done` / `undo_cancel`.
