# TAR — Telegram Agent Reminder

Bot Telegram đọc báo cáo tiến độ dự án viết bằng tiếng Việt, tự trích ra đầu
việc, rồi chủ động nhắc cho tới khi việc được đánh dấu xong.

Bạn dán nguyên văn báo cáo vào Telegram → bot trích các dòng trong khối
"Tiếp theo:", trình bảng để bạn duyệt → duyệt xong thì nó tự nhắc theo nhịp,
nhắc gắt hơn khi việc quá hạn. Ngoài ra hỏi đáp được về dữ liệu đã lưu
("20/7 tôi có việc gì không", "việc này hạn thứ mấy").

---

## Ba luồng xử lý

Dự án được chia theo ba job, đặt tên A/B/C và giữ nguyên tên đó trong log lẫn
docstring, để đọc log là biết đang ở luồng nào.

| Job | Nhiệm vụ | Có dùng LLM? | Vào bằng đâu |
|---|---|---|---|
| **A** | Quét việc tới hạn và gửi nhắc | Không | APScheduler, mỗi phút |
| **B** | Xử lý nút Đã xong / Nhắc sau / Hoàn tác | Không | callback từ Telegram |
| **C** | Trích đầu việc từ báo cáo + hỏi đáp | Có | tin nhắn text |

Chỉ Job C là graph LangGraph. Job A và B là code thuần — nhắc việc và bấm nút
không cần suy luận, cho LLM vào đó chỉ thêm độ trễ, thêm chi phí và thêm chỗ
để sai.

## Luồng Job C

```
                              ┌─ có "Tiếp theo:" ─→ extract_tasks ──→ ask_confirm
tin nhắn ─→ classify_message ─┤                        ↑   ↑         (interrupt,
                              └─ còn lại ──→ agent     │   │        chờ người dùng)
                                              ↕        │   │              │
                                            tools      │   │              ↓
                                              ↓        │   └─ chưa rõ ─ read_decision
                                            answer     └───── sửa lại ────┤
                                                                          ↓
                                                                      save_to_db
```

Điểm đáng chú ý: nhánh báo cáo **dừng lại chờ người duyệt** bằng `interrupt()`.
Bảng đầu việc không có nút bấm — bạn trả lời bằng tin nhắn thường ("ok",
"thiếu việc số 3", "thôi bỏ đi"), và node `read_decision` dùng một LLM riêng để
đọc ý định đó. Muốn sửa thì graph quay lại chính node `extract_tasks` với yêu
cầu sửa kèm theo, trích lại từ đầu; trả lời chưa rõ ý thì quay về `ask_confirm`
hỏi lại, bảng vẫn treo chờ.

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
| R3 | Nhịp nhắc tra theo bảng (ưu tiên × trạng thái), đọc từ cấu hình | `app/core/priority.py` |
| R4 | Việc thường tự nâng lên ưu tiên khi quá hạn, hoặc khi sắp tới hạn | `app/core/priority.py` |
| R6 | Chỉ nhắc trong khung giờ cho phép, ngoài giờ thì dồn sang sáng hôm sau | `app/core/priority.py` |
| R7 | `task_id` = hash(nhóm + nội dung đã bỏ `(hạn ...)`), nên gửi lại báo cáo — kể cả khi đã sửa hạn — chỉ cập nhật chứ không tạo bản trùng | `app/core/task_text.py` + `reminder_agent/graph.py` |
| R8 | Chỉ hoàn tác được trong vòng 24 giờ kể từ lúc đánh dấu xong | `persistence/proc/tasks.py` |
| R9 | Không đoán hạn; việc thiếu hạn thì hỏi lại đúng một lần | `prompts/extract_system.md` |

## Cấu trúc thư mục

```
app/                     Tầng ứng dụng — FastAPI, Telegram, lịch chạy
  core/                  Cấu hình, thời gian, quy tắc ưu tiên, chuẩn hoá nội dung việc
  routers/webhooks.py    Nhận update từ Telegram
  scheduler/runner.py    APScheduler: Job A + dọn checkpoint
  services/              Job A (reminder_service), Job B (callback_service)
  telegram/              Bot, bàn phím, soạn tin nhắn, gửi có retry
reminder_agent/          Job C — agent LangGraph
  config/models.yaml     Tham số LLM từng vai (KHÔNG chứa khoá bí mật)
  prompts/*.md           Prompt, tách khỏi code
  utils/tools/           Hai tool tra cứu, chỉ đọc
  graph.py               Toàn bộ node là method của ReminderAgent
persistence/             Tầng dữ liệu
  models/                Bảng SQLModel
  proc/                  Truy vấn, gọi qua asyncio.to_thread
  schema/*.sql           DDL, chạy tay một lần
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
psql "$DATABASE_URL" -f persistence/schema/01_task.sql
psql "$DATABASE_URL" -f persistence/schema/02_report.sql
```

Bảng checkpoint của LangGraph thì không cần làm gì, `checkpointer.setup()` tự
tạo lúc khởi động.

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
| `URGENT_PENDING_INTERVAL_MIN` | 5 | Nhịp nhắc việc gấp |
| `NORMAL_PENDING_INTERVAL_MIN` | 30 | Nhịp nhắc việc thường |
| `URGENT_SNOOZED_INTERVAL_MIN` | 10 | Nhịp sau khi bấm "Nhắc sau" (việc gấp) |
| `NORMAL_SNOOZED_INTERVAL_MIN` | 60 | Nhịp sau khi bấm "Nhắc sau" (việc thường) |
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

Bốn prompt, mỗi cái một file `.md`:

| File | Dùng ở |
|---|---|
| `extract_system.md` | Trích đầu việc từ báo cáo |
| `extract_retry.md` | Nối thêm vào prompt trên khi người dùng yêu cầu sửa |
| `agent_system.md` | Nhánh hỏi đáp |
| `decision_system.md` | Đọc ý định duyệt / sửa / bỏ |

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
