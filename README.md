# TAR — Telegram Agent Reminder

> ⚠️ **Repo đang được chuyển đổi thành hệ agentic RAG hỏi đáp tiến độ dự án.**
> Chức năng **nhắc việc đã bị gỡ bỏ** (Job A, lịch quét, tin nhắc gộp, khung giờ
> nhắc). Bản cuối cùng còn chạy đầy đủ tính năng nhắc việc nằm ở nhánh `main`
> trước lần chuyển đổi. Kế hoạch: [docs/plan-refactor.md](docs/plan-refactor.md).
> README này sẽ được viết lại theo sản phẩm mới.

Bot Telegram đọc báo cáo tiến độ dự án viết bằng tiếng Việt và tự trích ra đầu
việc.

Bạn dán nguyên văn báo cáo vào Telegram → bot trích các dòng trong khối
"Tiếp theo:", trình bảng để bạn duyệt → duyệt xong thì lưu lại. Báo xong, hủy
việc hay đổi hạn cũng nhắn thẳng cho bot ("TB-002 xong rồi", "dời QT-001 sang 2
tuần nữa"), và hỏi đáp được về dữ liệu đã lưu ("20/7 tôi có việc gì không").

---

## Luồng xử lý

Chỉ còn một luồng, vẫn giữ tên **Job C** trong log lẫn docstring:

| Job | Nhiệm vụ | Có dùng LLM? | Vào bằng đâu |
|---|---|---|---|
| **C** | Trích đầu việc từ báo cáo + hỏi đáp + cập nhật việc | Có | tin nhắn text |

> Từng có **Job A** quét việc tới hạn rồi gửi một tin nhắc gộp, và **Job B** xử
> lý nút inline "Đã xong / Nhắc sau / Hoàn tác". Cả hai đã bỏ hẳn. Việc nền duy
> nhất còn lại là dọn bảng checkpoint LangGraph lúc 3h sáng.

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
| R4 | Việc thường tự nâng lên ưu tiên khi quá hạn, hoặc khi sắp tới hạn — nay chỉ còn ảnh hưởng mức hiển thị trong tool tra cứu | `app/core/priority.py` |
| R7 | `task_id` = hash(nhóm + nội dung đã bỏ `(hạn ...)`), nên gửi lại báo cáo — kể cả khi đã sửa hạn — chỉ cập nhật chứ không tạo bản trùng | `app/core/task_text.py` + `reminder_agent/graph.py` |
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
  core/security.py       Chốt quyền admin, kiểm secret webhook
  routers/webhooks.py    Hai endpoint, hai bot, hai graph
  scheduler/runner.py    APScheduler: dọn checkpoint
  telegram/              bots, polling, download, keyboard, render, sender
TAR_agent/               Lõi agent — chạy được mà không cần Telegram
  graph_admin/           Luồng nạp tài liệu — có quyền GHI vào kho
  graph_client/          Luồng hỏi đáp — CHỈ ĐỌC
  utils/                 Dùng chung cho cả hai luồng
    config.py            Đọc .env + models.yaml, dựng LLM/embedding, nạp prompt
    models.yaml          Tham số LLM/embedding (KHÔNG chứa khoá bí mật)
    prompts/*.md         Prompt, tách khỏi code
persistence/             Tầng dữ liệu
  models/                Bảng SQLModel
  proc/                  Truy vấn, gọi qua asyncio.to_thread
  schema/schema.sql      Toàn bộ DDL trong một file, chạy tay một lần
tests/                   pytest cho phần logic thuần (không đụng DB, không gọi LLM)
run.py                   Điểm chạy local trên Windows
```

Chiều phụ thuộc chỉ đi một hướng — `app → TAR_agent → persistence`. Không có gì
trong `TAR_agent/` import ngược lên `app`, nhờ vậy lõi test được bằng pytest thuần
mà không phải giả lập aiogram. Hai graph con thì **không import lẫn nhau**: chung
package cha không có nghĩa là chung quyền.

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

Tinh chỉnh hành vi (đều có giá trị mặc định):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `ESCALATION_OVERDUE_DAYS` | 1 | Quá hạn bao nhiêu ngày thì nâng lên ưu tiên (R4) |
| `ESCALATION_DUE_SOON_DAYS` | 3 | Còn bao nhiêu ngày tới hạn thì nâng lên ưu tiên (R4) |
| `LOCAL_TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ cho toàn bộ tính toán |
| `CHECKPOINT_RETENTION_DAYS` | 14 | Giữ checkpoint LangGraph bao lâu (dọn lúc 3h sáng) |

> Chỉ đặt **khoá bí mật** trong `.env`. Tham số hành vi của LLM — model,
> temperature, timeout, số vòng gọi tool — nằm ở `TAR_agent/utils/models.yaml`;
> prompt nằm ở `TAR_agent/utils/prompts/*.md`. Nhờ vậy cả hai lên được git mà
> không kéo theo khoá.

## Prompt và model

Prompt nằm ở `TAR_agent/utils/prompts/`, mỗi cái một file `.md`, đặt tên theo
node gọi nó:

| File | Dùng ở | Lượt LLM mỗi câu hỏi |
|---|---|---|
| `client_system/identify_project.md` | Chọn dự án + tách câu hỏi | 1 |
| `client_system/agent_decide.md` | Lượt này có cần tra kho không | 1 |
| `client_system/gen_sql.md` | Sinh SQL, và sửa SQL khi Postgres nổ | 1–2 |
| `client_system/grade_docs.md` | Chấm lô đoạn tra được | 1 |
| `client_system/rewrite_query.md` | Đổi từ khoá khi tra hụt | 0–1 |
| `client_system/agent_select.md` | Giữ lại đúng phần trả lời được câu hỏi | 1 |
| `client_system/compose.md` | Soạn câu trả lời cuối + tự chấm | 1 |
| `admin_system/extract_header.md` | Ánh xạ tên cột của sheet .xlsx | 1/sheet, chỉ lúc nạp |
| `admin_system/extract_rows.md` | Trích dòng công việc từ .txt | N÷8, chỉ lúc nạp |

> `gen_sql.md` và `grade_docs.md` chạy **song song** trong cùng một lượt tra —
> xem `graph_client/tools/retrieval.py`. Một câu hỏi bình thường tốn 6 lượt LLM,
> trong đó 2 lượt chồng lên nhau về thời gian.
>
> `agent_decide.md` và `agent_select.md` là hai chế độ của cùng một node
> (`graph_client/nodes/agent.py`): một cái đứng trước `retrieve` để câu hỏi
> không cần tra được trả lời thẳng (lượt đó chỉ tốn 2 lượt LLM), một cái đứng
> sau để cắt phần dữ liệu lạc đề trước khi `compose` viết.

Prompt viết bằng tiếng Anh, giữ tiếng Việt ở chỗ yêu cầu bot đáp bằng tiếng Việt.

Biến trong prompt viết theo cú pháp `{{TÊN}}` (không phải `str.format`), để
prompt chứa dấu `{ }` của JSON hay code không bị hiểu nhầm là biến template.

> Prompt sửa ở `.md` **không** kích hoạt reload của uvicorn (nó chỉ theo dõi
> `*.py`), và `load_prompt` có cache. Sửa xong phải khởi động lại tiến trình.

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
