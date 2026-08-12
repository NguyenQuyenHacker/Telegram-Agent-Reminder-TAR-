# TAR — Telegram Agent Reminder

Bot Telegram đọc báo cáo tiến độ dự án viết bằng tiếng Việt, tự trích ra đầu
việc, rồi chủ động nhắc cho tới khi việc được đánh dấu xong.

Bạn dán nguyên văn báo cáo vào Telegram → bot trích các dòng trong khối
"Tiếp theo:", trình bảng để bạn duyệt → duyệt xong thì nó tự nhắc theo nhịp,
gom cả lô vào một tin, nhắc gắt hơn khi việc quá hạn. Báo xong, hủy việc hay đổi
hạn cũng nhắn thẳng cho bot ("TB-002 xong rồi", "dời QT-001 sang 2 tuần nữa"),
và hỏi đáp được về dữ liệu đã lưu ("20/7 tôi có việc gì không").

---

## Hai luồng xử lý

Dự án chia theo hai job, đặt tên A/C và giữ nguyên tên đó trong log lẫn
docstring, để đọc log là biết đang ở luồng nào.

| Job | Nhiệm vụ | Có dùng LLM? | Vào bằng đâu |
|---|---|---|---|
| **A** | Quét việc tới hạn và gửi một tin nhắc gộp | Không | APScheduler, mỗi phút |
| **C** | Trích đầu việc từ báo cáo + hỏi đáp + cập nhật việc | Có | tin nhắn text |

Chỉ Job C là graph LangGraph. Job A là code thuần — nhắc việc không cần suy
luận, cho LLM vào đó chỉ thêm độ trễ, thêm chi phí và thêm chỗ để sai.

> Từng có Job B xử lý nút inline "Đã xong / Nhắc sau / Hoàn tác". Đã bỏ hẳn:
> một lượt nhắc giờ là một tin gộp cho cả lô việc, không gắn nút cho từng việc
> được nữa. Mọi thao tác chuyển sang nhắn tin với agent.

## Luồng Job C

```
                              ┌─ có "Tiếp theo:" ─→ extract_tasks ──→ ask_confirm
tin nhắn ─→ classify_message ─┤                        ↑   ↑         (interrupt,
                              └─ còn lại ──→ agent     │   │        chờ người dùng)
                                              ↕        │   │              │
                                            tools      │   │              ↓
                                              ↓        │   └─ chưa rõ ─ read_decision
                                            answer     └───── sửa lại ────┤
                                              │                           ↓
                                    có đề xuất│                       save_to_db
                                              ↓
                                    ask_update_confirm ──→ read_update_decision
                                       (interrupt)                  │
                                                          duyệt ────┴──→ apply_updates
```

Hai nhánh có cùng hình dạng ở đoạn cuối vì cùng một nguyên tắc: **LLM chỉ đề
xuất, người dùng gật thì code mới ghi DB**. Nhánh báo cáo dừng chờ duyệt bảng
đầu việc; nhánh hỏi đáp dừng chờ duyệt đề xuất cập nhật của
`propose_task_update` — tool đó cố ý không ghi gì cả.

Không chỗ nào có nút bấm — bạn trả lời bằng tin nhắn thường ("ok", "thiếu việc
số 3", "thôi bỏ đi"), và node `read_decision` / `read_update_decision` dùng
chung một LLM riêng để đọc ý định đó. Muốn sửa bảng đầu việc thì graph quay lại
chính node `extract_tasks` với yêu cầu sửa kèm theo, trích lại từ đầu; trả lời
chưa rõ ý thì quay về `ask_confirm` hỏi lại, bảng vẫn treo chờ.

Riêng đề xuất cập nhật thì trả lời chưa rõ là **bỏ luôn đề xuất** chứ không hỏi
lại vòng vòng: dựng lại một đề xuất chỉ tốn một câu bạn nhắn, còn treo ở
`interrupt()` là nuốt mọi tin nhắn sau đó của bạn.

### Mỗi tin nhắn là một lần chạy graph

`interrupt()` không phải "tạm dừng" — nó ghi checkpoint rồi **thoát hẳn** khỏi
`ainvoke`. Giữa hai tin nhắn của bạn, không có tiến trình nào ngủ chờ, không tốn
RAM; toàn bộ tiến độ nằm trong bảng checkpoint ở Postgres, khoá theo
`thread_id = chat_id`. Server restart giữa chừng cũng không mất bảng đầu việc.

Nên một lượt duyệt gồm nhiều lần `ainvoke` độc lập, mỗi lần vào graph ở một chỗ
khác nhau:

| Bạn nhắn | Webhook gọi | Graph chạy từ đâu |
|---|---|---|
| dán báo cáo | `ainvoke({...state mới...})` | `START` → `extract_tasks` |
| "sửa việc 3" | `ainvoke(Command(resume=...))` | `ask_confirm` → `read_decision` → `extract_tasks` |
| "ok" | `ainvoke(Command(resume=...))` | `ask_confirm` → `read_decision` → `save_to_db` |

Hệ quả quan trọng khi sửa code: **node chứa `interrupt()` chạy lại từ dòng đầu
mỗi lần resume**, vì Python không lưu được trạng thái nửa chừng của một hàm. Lần
resume thì `interrupt()` trả về ngay thay vì dừng. Vì vậy `ask_confirm` cố ý
rỗng — chỉ mỗi `interrupt()` — còn gọi LLM và gửi tin nhắn đẩy hết sang
`read_decision`, node thường nên chạy đúng một lần. Đặt side effect nhầm vào
`ask_confirm` là mỗi câu trả lời tốn thêm một lời gọi LLM và một tin nhắn lặp.

## Quy tắc nghiệp vụ

Các quy tắc được đánh số R1–R9 và ghi thẳng vào docstring nơi thực thi, tìm
bằng cách grep số hiệu.

| Mã | Nội dung | Nơi thực thi |
|---|---|---|
| R1/R2 | Chỉ trích khối "Tiếp theo:", bỏ qua "Hiện trạng:" | `prompts/extract_system.md` |
| R3 | Nhịp nhắc đọc từ cấu hình (`PENDING_INTERVAL_MIN`), không hard-code | `app/core/priority.py` |
| R4 | Việc thường tự nâng lên ưu tiên khi quá hạn, hoặc khi sắp tới hạn | `app/core/priority.py` |
| R6 | Chỉ nhắc trong khung giờ cho phép, ngoài giờ thì dồn sang sáng hôm sau | `app/core/priority.py` |
| R7 | `task_id` = hash(nhóm + nội dung đã bỏ `(hạn ...)`), nên gửi lại báo cáo — kể cả khi đã sửa hạn — chỉ cập nhật chứ không tạo bản trùng | `app/core/task_text.py` + `reminder_agent/graph.py` |
| R8 | Chỉ hoàn tác được trong `UNDO_WINDOW_HOURS` kể từ lúc đánh dấu xong / hủy | `persistence/proc/tasks.py` |
| R9 | Không đoán hạn; việc thiếu hạn thì hỏi lại đúng một lần | `prompts/extract_system.md` |

## Mã việc

Mỗi việc có một mã ngắn dạng `TB-002` để bạn gõ lại trong tin nhắn. Tiền tố sinh
tự động từ tên nhóm: lấy chữ cái đầu của **mọi** từ rồi giữ hai chữ cuối —
"App Trưởng thôn, trưởng bản" → `atttb` → `TB`. Không có danh sách từ đệm nào
phải bảo trì (`app/core/task_code.py`), số thứ tự do bảng `group_code` đếm.

Mã này tách khỏi `task_id`: `task_id` là băm phục vụ R7, không ai gõ nổi. Mã chỉ
cấp lúc tạo mới và **không đổi** kể cả khi tên nhóm sau đó đổi — nó đã được in
ra cho bạn rồi.

## Cấu trúc thư mục

```
app/                     Tầng ứng dụng — FastAPI, Telegram, lịch chạy
  core/                  Cấu hình, thời gian, ưu tiên, mã việc, gom rổ tin nhắc
  routers/webhooks.py    Nhận update từ Telegram
  scheduler/runner.py    APScheduler: Job A + dọn checkpoint
  services/              Job A (reminder_service)
  telegram/              Bot, soạn tin nhắn, gửi có retry
reminder_agent/          Job C — agent LangGraph
  config/models.yaml     Tham số LLM từng vai (KHÔNG chứa khoá bí mật)
  prompts/*.md           Prompt, tách khỏi code
  utils/tools/           Hai tool tra cứu + một tool đề xuất cập nhật
  graph.py               Toàn bộ node là method của ReminderAgent
persistence/             Tầng dữ liệu
  models/                Bảng SQLModel
  proc/                  Truy vấn, gọi qua asyncio.to_thread
  schema/schema.sql      Toàn bộ DDL trong một file, chạy tay một lần
tests/                   pytest cho phần logic thuần (không đụng DB, không gọi LLM)
run.py                   Điểm chạy local trên Windows
```

## Chạy local

Cần Python 3.12, một Postgres (dự án dùng Neon), bot token Telegram và
Google API key.

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt

copy .env.example .env                             # rồi điền giá trị thật
```

Tạo bảng — chưa có bước tự động, chạy tay một lần vào Postgres của bạn:

```bash
psql "$DATABASE_URL" -f persistence/schema/schema.sql
```

Toàn bộ DDL nằm trong đúng file đó, không có file migration tăng dần. Nếu bạn
đang giữ DB từ bản cũ (còn cột `remind_interval_min`, còn trạng thái `snoozed`)
thì file này toàn `CREATE TABLE IF NOT EXISTS` nên **không** cập nhật bảng sẵn
có — dựng lại:

```bash
psql "$DATABASE_URL" -c 'DROP TABLE IF EXISTS task, report;'
psql "$DATABASE_URL" -f persistence/schema/schema.sql
```

Bảng checkpoint của LangGraph thì không cần làm gì, `checkpointer.setup()` tự
tạo lúc khởi động.

Chạy test:

```bash
pytest tests -q
```

Chạy:

```bash
python run.py
```

**Trên Windows bắt buộc dùng `python run.py`, không gọi `uvicorn app.main:app`
trực tiếp.** psycopg async không chạy được trên ProactorEventLoop (mặc định của
Windows); `run.py` đổi sang WindowsSelectorEventLoopPolicy *trước khi* uvicorn
tạo event loop. Gọi uvicorn thẳng sẽ đổ `InterfaceError`. Trên Linux không cần
file này.

Chưa đặt `TELEGRAM_WEBHOOK_URL` thì app tự chuyển sang chế độ **polling**, vẫn
nhận được tin nhắn khi dev ở máy local không có domain public.

## Biến môi trường

Xem `.env.example` để có bản đầy đủ kèm chú thích. Bắt buộc:

| Biến | Ý nghĩa |
|---|---|
| `BOT_TOKEN` | Token bot Telegram |
| `DATABASE_URL` | DSN Postgres, dạng `postgresql+psycopg://...` |
| `GOOGLE_API_KEY` | Khoá Gemini |
| `TELEGRAM_CHAT_ID` | Chat nhận nhắc việc. Chưa biết thì để trống, nhắn cho bot một tin rồi lấy |

Tinh chỉnh hành vi (đều có giá trị mặc định):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PENDING_INTERVAL_MIN` | 30 | Nhịp nhắc, dùng chung cho mọi việc chưa xong |
| `UNDO_WINDOW_HOURS` | 24 | Hoàn tác "đã xong" / "đã hủy" trong ngần này giờ (R8) |
| `ESCALATION_OVERDUE_DAYS` | 1 | Quá hạn bao nhiêu ngày thì nâng lên ưu tiên (R4) |
| `ESCALATION_DUE_SOON_DAYS` | 3 | Còn bao nhiêu ngày tới hạn thì nâng lên ưu tiên (R4) |
| `REMINDER_WINDOW_START_HOUR` | 8 | Đầu khung giờ được nhắc |
| `REMINDER_WINDOW_END_HOUR` | 18 | Cuối khung giờ được nhắc |
| `LOCAL_TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ cho toàn bộ tính toán |
| `JOB_A_INTERVAL_MINUTES` | 1 | Chu kỳ quét việc tới hạn |
| `CHECKPOINT_RETENTION_DAYS` | 14 | Giữ checkpoint LangGraph bao lâu (dọn lúc 3h sáng) |

> Chỉ đặt **khoá bí mật** trong `.env`. Tham số hành vi của LLM — model,
> temperature, timeout, số vòng gọi tool — nằm ở
> `reminder_agent/config/models.yaml`; prompt nằm ở `reminder_agent/prompts/*.md`.
> Nhờ vậy `models.yaml` và prompt lên được git mà không kéo theo khoá.

## Prompt và model

Năm prompt, mỗi cái một file `.md`:

| File | Dùng ở |
|---|---|
| `extract_system.md` | Trích đầu việc từ báo cáo |
| `extract_retry.md` | Nối thêm vào prompt trên khi người dùng yêu cầu sửa |
| `agent_system.md` | Nhánh hỏi đáp |
| `decision_system.md` | Đọc ý định duyệt / sửa / bỏ (dùng cho cả hai chốt duyệt) |
| `_date_rules.md` | Luật quy đổi "2 ngày nữa" → ngày ISO, nối vào **cả hai** prompt trên |

Prompt viết bằng tiếng Anh, nhưng cố ý giữ tiếng Việt ở ba chỗ: từ khoá cần
khớp mặt chữ trong báo cáo (`Tiếp theo:`, `Hiện trạng:`, `ƯU TIÊN`), ví dụ câu
trả lời của người dùng, và yêu cầu bot đáp bằng tiếng Việt.

Biến trong prompt viết theo cú pháp Langfuse `{{TÊN}}`. Ngày hiện tại được bơm
vào cả nhánh trích lẫn nhánh hỏi đáp qua `{{TODAY}}` — thiếu nó thì LLM tự bịa
năm cho những chuỗi kiểu "19/7". Thứ trong tuần thì Python tính sẵn rồi đưa vào
kết quả tool (`due_weekday`), không để LLM tự suy từ ngày.

> Prompt sửa ở `.md` **không** kích hoạt reload của uvicorn (nó chỉ theo dõi
> `*.py`), và `load_prompt` có cache. Sửa xong phải khởi động lại tiến trình.

## Langfuse (tuỳ chọn)

Đặt `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` thì
prompt được lấy từ Langfuse (label `production`) và toàn bộ lời gọi graph được
trace. Không đặt thì dùng file `.md`, không trace. Mất mạng hay sai khoá cũng
không làm chết agent — Langfuse tự rơi về bản `.md`.

Hai chức năng, hai mức phụ thuộc khác nhau:

| Muốn gì | Cần cài |
|---|---|
| Sửa prompt trên UI | `langfuse` |
| Thêm cả **trace** | `langfuse` **và** `langchain` |

Chỗ dễ mất thời gian: `langfuse.langchain.CallbackHandler` import từ gói
`langchain`, `langchain-core` sẵn có trong `requirements.txt` **không đủ**. Thiếu
nó thì `_with_tracing` nuốt `ModuleNotFoundError` và log cảnh báo rồi chạy tiếp
không trace — bot vẫn hoạt động bình thường nên rất dễ tưởng là đã bật.

```bash
pip install "langfuse>=3.0.0" "langchain>=0.3,<0.4"
```

`app/main.py` gọi `load_dotenv()` chính vì Langfuse: pydantic-settings đọc `.env`
để điền field của `Settings` nhưng **không ghi vào `os.environ`**, mà Langfuse
lại đọc thẳng `os.getenv("LANGFUSE_*")`. Thiếu dòng đó thì khoá có trong `.env`
vẫn coi như không có.

### Bẫy tốc độ: prompt chưa đẩy lên Langfuse

Hỏi Langfuse một prompt **không tồn tại** tốn **1–3 giây**. Nó không raise — trả
bản `.md` rồi thôi — nhưng cũng **không nhớ là hỏng**, nên lượt chat sau lại đi
hỏi lại y hệt. Mỗi lượt chat nạp 2 prompt ⇒ mất vài giây trước khi kịp gọi LLM.

`loader.py` xử bằng cách nhớ tên hỏng (`_missing_on_langfuse`) và thôi không hỏi
lại trong suốt tiến trình, còn `warm_up()` trả trước khoản chờ đó lúc khởi động.
Hệ quả:

- Khởi động chậm thêm ~2 giây cho **mỗi** prompt chưa có trên Langfuse.
- Sau đó mọi lượt `load_prompt` là 0ms.
- **Đẩy prompt lên Langfuse rồi thì phải khởi động lại** để nó được hỏi lại —
  cùng ràng buộc với việc sửa file `.md`.

Không muốn chờ lúc khởi động thì hoặc đẩy đủ 5 prompt lên Langfuse, hoặc bỏ
trống `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (mất luôn trace).

### Đọc trace

Trace được cắt theo **mỗi lần `ainvoke`**, tức mỗi tin nhắn Telegram một trace
riêng — xem bảng ở mục [Mỗi tin nhắn là một lần chạy graph](#mỗi-tin-nhắn-là-một-lần-chạy-graph).
Trace của một lượt resume mở đầu bằng `__start__ → ask_confirm`; đó là điểm vào
của lượt đó chứ không phải node `START` của graph.

Vài thứ cố ý **không** xuất hiện trong trace:

- `classify_message` — chạy ở webhook, trước khi vào graph, và chỉ so keyword nên
  không có gì để xem.
- `route_entry` / `route_after_confirm` — hàm điều hướng không phải node, chỉ
  hiện ra dưới dạng mũi tên.
- `_write` — span nội bộ của LangGraph khi ghi state, bỏ qua được.

View **Expanded** vẽ đúng những node đã chạy trong trace đó; muốn nhìn toàn bộ
định nghĩa graph thì chuyển sang **Aggregated**.

## Triển khai

`Dockerfile` có sẵn, chạy được trên Railway hoặc bất kỳ chỗ nào cấp domain
HTTPS:

```bash
docker build -t tar .
docker run --env-file .env -p 8000:8000 tar
```

Đặt `TELEGRAM_WEBHOOK_URL` thành domain public → app tự gọi `setWebhook` lúc
khởi động và chuyển từ polling sang webhook. Nên đặt luôn
`TELEGRAM_WEBHOOK_SECRET`: Telegram gửi lại chuỗi này ở header
`X-Telegram-Bot-Api-Secret-Token` và app từ chối 403 nếu không khớp.

Endpoint kiểm tra sống: `GET /health`.
