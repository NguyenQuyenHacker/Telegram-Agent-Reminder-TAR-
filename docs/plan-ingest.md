# Workflow luồng nạp + Cơ sở dữ liệu

> **Đã cài xong bước 0–4** (DB, proc, loader, splitter, embed, state, 7 node, graph).
> Còn lại bước 5 — tầng Telegram.
>
> **Hai chỗ plan này viết SAI, phát hiện lúc chạy thật:**
>
> 1. `ainvoke` **không** trả về khoá `__interrupt__` ở langgraph 0.2.60 (bản đang ghim).
>    Điểm dừng chỉ thấy qua `aget_state()`. Đã bọc thành `shared/graph_io.py:pending_interrupt()`
>    — tầng Telegram gọi hàm đó, không tự đọc state.
> 2. `models/text-embedding-004` **đã bị Google gỡ** (404). Thay bằng
>    `models/gemini-embedding-001` + `output_dimensionality=768`, nhờ vậy cột
>    `VECTOR(768)` giữ nguyên không phải đổi.
>
> **Quyết định sau bước 4, chưa cập nhật vào các phần dưới đây:**
> - **Bỏ hẳn định dạng .pdf.** Chỉ còn `.txt` và `.xlsx`. `loaders/pdf.py` đã xoá,
>   `FileKind` và `CHECK (file_kind IN (...))` đã bỏ `'pdf'`.
> - **`.xlsx` đổi sang `UnstructuredExcelLoader`**, không tự viết bằng `openpyxl` nữa.
>   `loaders/txt.py` và `loaders/xlsx.py` gộp chung vào `loaders/xlsx.py`.
>   **CHƯA KIỂM CHỨNG**: `.load()` cần gói `pandas`, chưa cài trong `tar_env`; rủi ro
>   ngày-tháng-ra-serial-number nêu ở A5 dưới đây **vẫn treo**, xem cảnh báo trong
>   `graph_admin/utils/loaders/xlsx.py` và `tests/test_loaders.py::TestLoadXlsx`.

## Context

Repo đã đổi sản phẩm: từ bot nhắc việc sang kho tri thức hỏi đáp. Khung thư mục đã tái cấu
trúc xong (`graph_admin/`, `graph_client/`, `shared/`, `app/`, `persistence/`), toàn bộ còn
là stub `NotImplementedError`. DB chưa dựng trên Neon.

Đợt này làm **workflow của graph admin** và **database**. Tầng Telegram để sau.

### Đã chốt

| | |
|---|---|
| Tạo dự án | Lệnh `/duan <tên>`. Lúc upload không có nút "tạo mới" |
| Gán dự án | Bàn phím liệt kê dự án đã có, hỏi mọi file, không có cơ chế dính |
| Chưa có dự án nào | Từ chối file, bảo gõ `/duan <tên>` trước |
| Danh tính tài liệu | `(dự án, tên file)` — nạp lại cùng tên là ghi đè, **có hỏi xác nhận** |
| Đọc file | **Loader của LangChain** (`langchain-community`), `.xlsx` tự đọc bằng `openpyxl` |
| Cắt chunk | `RecursiveCharacterTextSplitter` |
| Metadata chunk | Cột `JSONB`, không phải `heading_path TEXT` |
| Mốc dữ liệu | Đoán từ tên file, không ra thì hôm nay, luôn in ra để soát |
| `progress_item` | Không đụng tới đợt này |

---

## Mô hình cập nhật: ghi đè theo tên file

Một tài liệu nhận diện bằng **tên file trong phạm vi một dự án**. Nạp lại cùng tên →
hỏi xác nhận → xoá sạch chunk cũ, ghi chunk mới, tên trong DB **giữ nguyên**.

```
Nạp lần 1:  TiendoT6.xlsx      → tạo tài liệu, 42 chunk
Nạp lần 2:  TiendoT6(1).xlsx   → CÙNG tài liệu (bỏ "(1)")
                                 hỏi "ghi đè?" → xoá 42 chunk, ghi 45 chunk
                                 tên trong DB vẫn là "TiendoT6.xlsx"
```

`(n)` là cái Telegram/Windows tự thêm khi tải trùng tên — vẫn là bản sửa của cùng file.

### Lịch sử không mất

`TiendoT6.xlsx` và `TiendoT7.xlsx` là **hai tài liệu khác nhau, cùng sống**. Ghi đè chỉ xảy
ra khi sửa lại chính bản tháng 6. Kho vẫn trả lời được *"tuần này so với tuần trước"*.

Đổi lại, tra cứu có thể trả về cả đoạn T6 lẫn T7 — nên **mỗi chunk mang `as_of_date`** và
câu trả lời bắt buộc trích mốc. Bản cũ vẫn lọt vào ngữ cảnh được, nhưng nó không giả vờ là
số mới.

---

# PHẦN A — WORKFLOW

## A1. Hình dạng graph

```
                                    ┌──── unchanged / cancelled ────┐
                                    │                                ▼
START ─ route ─┬─ ask_project ─ check_file ─ parse ─ store ─────── report ─ END
               │       │            │           │                    ▲
               │       └── lỗi ─────┴───────────┴────────────────────┤
               └─ handle_text ───────────────────────────────────────┘

           interrupt #1: chọn dự án     interrupt #2: xác nhận ghi đè
```

**Thứ tự này khác bản trước.** `check_file` cần `project_id` mới tra được
`(project_id, file_name)`, nên `ask_project` phải chạy trước — và `parse` lùi xuống sau
cùng. Hoá ra lại hay: **không bao giờ đọc và embed một file mà admin sắp từ chối ghi đè**.

Không có node kiểm định dạng: `app/telegram/download.py` đã chặn đuôi file lạ và file quá
`max_upload_mb` **trước khi** dựng `UploadedFile`. File vào tới lõi là file hợp lệ, kiểm lại
lần nữa chỉ là code chết.

`report` là **cửa ra duy nhất** — không có đường nào lượt chạy kết thúc mà admin không nhận
được gì, và đó cũng là chỗ dọn file tạm.

## A2. Luồng chạy, đầu tới cuối

```
admin: /duan App Trưởng thôn
   route ──► handle_text ──► INSERT project ──► report
bot:   ✓ Đã tạo dự án "App Trưởng thôn"

admin: [TiendoT6.xlsx]          ← download.py đã chặn đuôi lạ / file quá nặng
   route ──► ask_project
             ├ đọc danh sách dự án             3 dự án
             └ DỪNG                            ← interrupt #1
bot:   Nạp TiendoT6.xlsx vào dự án nào?
       [ App Trưởng thôn ] [ Hệ thống báo cáo ] [ Đội xe ]

admin: (bấm "App Trưởng thôn")
         ──► check_file
             ├ base_name("TiendoT6.xlsx")      "TiendoT6.xlsx"
             ├ sha256 đọc từ file tạm          "a3f9…"
             └ tra (project_id, "TiendoT6.xlsx")   chưa có → đi tiếp
         ──► parse
             ├ loader .xlsx                    3 sheet → 3 Document
             ├ splitter                        42 chunk
             └ đoán mốc từ tên file            30/06/2026
         ──► store
             ├ embed 42 đoạn                   [gọi mạng, chia lô]
             └ INSERT tài liệu + 42 chunk      [MỘT transaction]
         ──► report                            [xoá file tạm]
bot:   ✓ Nạp 42 đoạn · App Trưởng thôn · mốc dữ liệu 30/06/2026


admin: [TiendoT6(1).xlsx]        ← bản sửa
         ──► ask_project ──► (bấm App Trưởng thôn)
         ──► check_file
             ├ base_name → "TiendoT6.xlsx"     ĐÃ CÓ
             ├ sha256 khác bản cũ?             khác
             └ DỪNG                            ← interrupt #2
bot:   TiendoT6.xlsx đã có trong App Trưởng thôn (42 đoạn, nạp 05/07).
       Ghi đè bằng bản mới?
       [ Ghi đè ]  [ Huỷ ]

admin: (bấm "Ghi đè")
         ──► parse ──► store
             ├ DELETE 42 chunk cũ
             └ UPDATE tài liệu + INSERT 45 chunk   [MỘT transaction]
bot:   ✓ Cập nhật TiendoT6.xlsx · 42 → 45 đoạn · mốc 30/06/2026
```

## A3. Từng node — `graph_admin/nodes/`

Node **mỏng**: đọc state → gọi `utils/` → trả dict cập nhật state. Không node nào gửi tin
nhắn; chúng append event vào `outbox`, tầng Telegram đọc rồi render.

| node | làm | ghi vào state |
|---|---|---|
| `route.py` | Không I/O. `choose_branch` trả `"ask_project"` hoặc `"handle_text"` | — |
| `ask_project.py` | Đọc danh sách dự án; rỗng → `error`. Còn lại **interrupt #1** | `project_id` `project_name` \| `error` |
| `check_file.py` | `base_name` · `sha256` · tra `(project_id, base_name)`; trùng hash → `unchanged`; khác → **interrupt #2** | `base_name` `content_sha256` `existing_id` `outcome` |
| `parse.py` | Loader → `list[Document]` → splitter → chunks; đoán mốc | `file_kind` `documents` `chunks` `as_of_date` \| `error` |
| `store.py` | Embed → ghi/ghi đè trong một transaction | `document_id` `chunk_count` |
| `report.py` | Dựng event; **xoá file tạm** | `outbox` |
| `handle_text.py` | `/duan <tên>` tạo · `/duan` liệt kê · còn lại → gợi ý | `outbox` |

`outcome` ∈ `"created"` · `"updated"` · `"unchanged"` · `"cancelled"`.

### `check_file` — dựng danh tính rồi tra, ba lối ra

```python
base = filename.base_name(state["upload"].file_name)   # bỏ "(n)"
sha  = sha256_of(state["upload"].path)                 # đọc file, chưa parse
existing = documents.find_by_name(project_id, base)

if existing is None:
    return {}                                    # đi thẳng parse → "created"

if existing.content_sha256 == sha:
    return {"outcome": "unchanged"}              # → report. KHÔNG hỏi, không tốn gì

choice = interrupt({"kind": "confirm_overwrite", "data": {...}})   # ← interrupt #2
if not choice["overwrite"]:
    return {"outcome": "cancelled"}              # → report
return {"existing_id": existing.document_id, "outcome": "updated"}  # → parse
```

Nhánh giữa quan trọng: hash tính từ bytes thô nên không cần parse. Gửi nhầm lại đúng file cũ
thì bot im lặng bỏ qua, không làm phiền bằng một câu hỏi vô nghĩa.

### Node có `interrupt` phải sạch tuyệt đối

`ask_project` và `check_file` đều chứa `interrupt()`. LangGraph chạy lại node từ **dòng 1**
khi resume, nên mọi tác dụng phụ ở đó sẽ chạy hai lần. Cả hai node chỉ được chứa thao tác
**đọc** (đọc lại vô hại) và xử lý giá trị trả về.

Payload đi ra qua `__interrupt__` của `ainvoke` — tầng Telegram đọc rồi dựng bàn phím.

### `store` — thứ tự KHÔNG được đảo

```
await shared.embed.embed_documents(...)          [gọi mạng, chia lô]
await to_thread(writer.save, ...)                [MỘT transaction]
    outcome == "updated"?  DELETE chunk cũ + UPDATE tài liệu
    còn lại?               INSERT tài liệu
    + INSERT chunk mới
```

Xoá-rồi-ghi phải trong **một** transaction. Xoá chunk cũ xong mà rơi giữa chừng là tài liệu
còn nguyên trong `source_document` nhưng **0 chunk** — tra không ra gì, mà nạp lại thì hash
trùng nên rơi vào nhánh `unchanged` và bị bỏ qua. Kẹt cứng, phải xoá tay mới gỡ.

### `handle_text` — nhánh tin nhắn chữ, và là chỗ DUY NHẤT tạo dự án

Nhánh còn lại của `route`: admin gõ chữ chứ không gửi file.

```
/duan <tên>   → shared.projects.create_project   → event project_created
/duan         → liệt kê dự án kèm số tài liệu    → event project_list
còn lại       → event hint ("gửi file .txt/.pdf/.xlsx đi")
```

Không gọi LLM — ba nhánh cố định không đáng một lượt gọi model. Đây cũng là chỗ dành sẵn cho
vòng ReAct quản lý kho khi dựng tới; lúc đó `route` và `graph.py` không đổi một dòng.

**Tạo dự án chỉ xảy ra ở đây.** Nạp file không bao giờ tự tạo, nếu không một cú gõ nhầm sinh
ra dự án rác đã có tài liệu nằm trong.

> Tên `command.py` bị loại vì trùng khái niệm với `langgraph.types.Command` — lớp dùng để
> resume sau `interrupt()`. Hai thứ khác hẳn nhau, để cùng tên là đọc code phải đoán.

### `error` là MÃ, không phải câu chữ

`"no_projects"`, `"empty_document"` (PDF scan ảnh, file rỗng), `"parse_failed"` (file hỏng),
`"temp_file_gone"`. `render.py` mới dịch sang tiếng Việt. Lõi không viết văn cho người đọc.

Không có `"unsupported_format"` hay `"file_too_large"` — hai thứ đó `download.py` chặn bằng
`UploadRejected` trước khi graph chạy.

## A4. File đi vào lõi bằng ĐƯỜNG DẪN, không phải bytes

```python
@dataclass(frozen=True)
class UploadedFile:
    file_name: str
    path: Path          # file tạm, tầng Telegram đã ghi ra đĩa
    size_bytes: int
```

Vì sao không giữ `bytes` trong state: LangGraph ghi checkpoint sau **mỗi** node, và có hai
điểm dừng chờ admin bấm. Giữ bytes nghĩa là mỗi lượt nạp đẩy tới 20MB vào bảng checkpoint
của Neon, nằm đó tới khi job dọn chạy.

Đường dẫn cũng đúng với ranh giới đã đặt: lõi nhận vào đường dẫn file hoặc câu hỏi, không
biết file từ Telegram hay từ đâu tới.

**Hai hệ quả phải chấp nhận:**
- Chỉ đúng khi chạy **một tiến trình**. Scale ra nhiều instance thì file tạm không nằm cùng
  máy với lượt resume — lúc đó phải đổi sang object storage.
- App restart giữa lúc admin chưa bấm → file tạm mất. `parse` phải kiểm `path.exists()` và
  trả `error: temp_file_gone` ("gửi lại file"), không được ném traceback.

Dọn file tạm ở `report` (cửa ra duy nhất) trong `finally`, cộng một lượt quét file tạm cũ
lúc khởi động.

## A5. `graph_admin/utils/`

| file | việc |
|---|---|
| `loaders/txt.py` `loaders/pdf.py` | Bọc `TextLoader` / `PyPDFLoader` của `langchain_community` |
| `loaders/xlsx.py` | **Tự viết** — `openpyxl`, trả `list[Document]`, mỗi sheet một Document |
| `loaders/__init__.py` | `load(path, file_kind) -> list[Document]` · `SUPPORTED` · `file_kind()` |
| `split.py` | `RecursiveCharacterTextSplitter`, đọc cấu hình từ `shared/models.yaml` |
| `filename.py` | `base_name()` bỏ `(n)` · `guess_as_of()` bắt `T6`, `2026-06-30`, `thang6`, `Tuan23` |
| `writer.py` | `save()` — nhánh created / updated, một transaction |

Bỏ `convert/` và `chunk.py` cũ.

### `.xlsx` tự đọc, không dùng `UnstructuredExcelLoader`

`UnstructuredExcelLoader` đổi bảng tính thành HTML rồi mới lấy text — ô ngày `30/06/2026`
có thể ra `45838` (serial number của Excel) hoặc `06/30/26` tuỳ locale, ô gộp bị trải ra
hoặc bỏ trống không báo. Đúng cái bạn cảnh báo từ đầu.

Tự đọc bằng `openpyxl` thì đọc thẳng `cell.value` — `datetime` ra `datetime`, số ra số. Map
cột sang bảng Markdown trong `page_content`, `metadata` giữ `{"sheet": ..., "header_row": ...}`.

Nó vẫn **là** một loader đúng nghĩa: có `.load()`, trả `list[Document]`, cắm vừa splitter.

## A6. `shared/embed.py`

`embed_documents` dùng `task_type=RETRIEVAL_DOCUMENT`, `embed_query` dùng `RETRIEVAL_QUERY`.
Dùng nhầm một kiểu cho cả hai là mất chất lượng tìm kiếm mà **không có lỗi nào báo**.

Tự chia lô bên trong: Gemini có trần số văn bản mỗi request; một file 300 chunk mà gọi 300
lần là chậm gấp bội và dễ dính rate limit.

---

# PHẦN B — DATABASE

```
        project                source_document                doc_chunk
   ┌──────────────┐          ┌──────────────────┐         ┌───────────────┐
   │ project_id PK│◄────┐    │ document_id   PK │◄───┐    │ chunk_id   PK │
   │ name         │     └────┤ project_id    FK │    └────┤ document_id FK│
   │ normalized_… │          │ file_name        │         │ project_id    │
   └──────────────┘          │ content_sha256   │         │ as_of_date    │
                             │ as_of_date       │         │ content       │
     vài chục dòng           │ documents JSONB  │         │ metadata JSONB│
                             └──────────────────┘         │ embedding     │
                                  vài trăm dòng           └───────────────┘
                                                         vài chục nghìn dòng
```

## B1. `project`

| cột | kiểu | nghĩa |
|---|---|---|
| `project_id` | `UUID` **PK** | `uuid5(NS, normalized_name)` — **không random** |
| `name` | `TEXT NOT NULL` | Tên hiển thị, giữ nguyên admin gõ: `"📱 App Trưởng thôn"` |
| `normalized_name` | `TEXT NOT NULL UNIQUE` | Tên chuẩn hoá: `"app truong thon"` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | mặc định `now()` |

**`normalized_name`** — cùng một dự án, ba người gõ ba kiểu:
```
"App Trưởng thôn"    ─┐
"2/ app trưởng thôn"  ├─► normalize ─► "app truong thon" ─► cùng MỘT project_id
"📱 APP TRƯỞNG THÔN" ─┘
```
Hàm chuẩn hoá ở `shared/text.py` (đã viết, đã test) bỏ dấu tiếng Việt, hạ chữ thường, bỏ
emoji, bỏ số thứ tự đầu dòng, gộp khoảng trắng.

`UNIQUE` đặt ở cột này chứ không ở `name`: đặt ở `name` thì `"Đội xe"` và `"đội xe "` lọt
thành hai dự án, tài liệu chia đôi, tra bên nào cũng thiếu.

**`project_id` không random** — `uuid5` là hàm băm, cùng đầu vào luôn ra cùng UUID. Biết tên
là biết id, không cần tra DB.

## B2. `source_document`

Mỗi dòng là một tài liệu, nhận diện bằng **`(project_id, file_name)`**.

| cột | kiểu | nghĩa |
|---|---|---|
| `document_id` | `UUID` **PK** | `uuid5(NS_DOC, f"{project_id}:{file_name}")` |
| `project_id` | `UUID NOT NULL` **FK** → `project` | Tài liệu thuộc dự án nào |
| `file_name` | `TEXT NOT NULL` | Tên đã bỏ `(n)`: `"TiendoT6.xlsx"`. Dùng để **trích nguồn** |
| `file_kind` | `TEXT NOT NULL` | `'txt'`\|`'pdf'`\|`'xlsx'`, có `CHECK` |
| `content_sha256` | `TEXT NOT NULL` | Băm **bytes thô**. Để biết nội dung có đổi không |
| `as_of_date` | `DATE NOT NULL` | **Mốc dữ liệu** |
| `documents` | `JSONB NOT NULL` | Đầu ra thô của loader |
| `chunk_count` | `INTEGER` | Số đoạn hiện có |
| `uploaded_by` | `BIGINT NOT NULL` | Telegram user id của admin nạp gần nhất |
| `uploaded_at` | `TIMESTAMPTZ` | Lần ghi gần nhất |
| | | `UNIQUE (project_id, file_name)` |

### ⚠ Bốn chỗ khác bản nháp hiện tại

```sql
content_sha256 TEXT NOT NULL UNIQUE      -- ← BỎ UNIQUE
markdown       TEXT NOT NULL             -- ← ĐỔI thành documents JSONB
...
UNIQUE (project_id, file_name)           -- ← THÊM: đây mới là danh tính
```

1. **`content_sha256` không còn `UNIQUE`.** Danh tính là tên file. Giữ hash chỉ để trả lời
   "nội dung có đổi không" — nếu vẫn `UNIQUE` thì hai tài liệu khác tên mà trùng nội dung bị
   chặn vô cớ.
2. **Thêm `UNIQUE (project_id, file_name)`.** Đây là thứ khiến "nạp lại cùng tên" thành ghi
   đè chứ không phải tạo bản trùng.
3. **`document_id` đổi công thức** — từ `uuid5(NS_DOC, sha256)` sang
   `uuid5(NS_DOC, f"{project_id}:{file_name}")`. Suy từ **tên**, không phải nội dung: ghi đè
   giữ nguyên `document_id`, nên mọi tham chiếu tới tài liệu vẫn đúng sau khi nội dung đổi.
4. **`markdown TEXT` → `documents JSONB`.** Không còn khâu convert sang Markdown nữa; thứ
   đáng giữ là đầu ra của loader: `[{"page_content": ..., "metadata": {...}}, …]`. Giữ cả
   metadata thì tái index (đổi `chunk_size`, đổi model embedding) không mất số trang, tên
   sheet — mà không phải bắt admin gửi lại file.

### `as_of_date` ≠ `uploaded_at`

Cột dễ hiểu nhầm nhất. File báo cáo tháng 6 nạp lên tháng 8: `as_of_date = 30/06`,
`uploaded_at = ngày tháng 8`. Hai câu hỏi khác nhau:
- "số liệu này tính tới ngày nào" → `as_of_date`
- "ai nạp, lúc nào" → `uploaded_by` + `uploaded_at`

Gộp làm một là mọi so sánh theo thời gian sai hết — client hỏi "tuần này so với tuần trước"
sẽ lấy nhầm file cũ tưởng là mới.

## B3. `doc_chunk`

Bảng to nhất — một file sinh vài trăm dòng.

| cột | kiểu | nghĩa |
|---|---|---|
| `chunk_id` | `UUID` **PK** | |
| `document_id` | `UUID NOT NULL` **FK** `ON DELETE CASCADE` | Cắt từ tài liệu nào |
| `project_id` | `UUID NOT NULL` | **Lặp cố ý** |
| `as_of_date` | `DATE NOT NULL` | **Lặp cố ý** |
| `chunk_index` | `INTEGER NOT NULL` | Thứ tự trong file: 0, 1, 2… |
| `content` | `TEXT NOT NULL` | `page_content` của `Document` |
| `metadata` | `JSONB NOT NULL DEFAULT '{}'` | `metadata` của `Document` |
| `embedding` | `VECTOR(768) NOT NULL` | Vector ngữ nghĩa |
| | | `UNIQUE (document_id, chunk_index)` |

**`project_id` lặp lại** — suy được qua `JOIN`, nhưng mọi truy vấn tìm kiếm **bắt buộc** lọc
theo dự án. Phải `JOIN` mới lọc được thì sớm muộn có người quên viết — hậu quả là client hỏi
dự án A nhận câu trả lời dựng từ tài liệu dự án B, mà câu đó **trông vẫn rất thật**.

**`as_of_date` lặp lại (cột mới)** — cùng lý do, và vì đây là thứ chữa vấn đề "bản nào mới
hơn": tìm kiếm trả về cả đoạn T6 lẫn T7 thì mỗi đoạn tự mang mốc, câu trả lời trích được
ngày. Cột **bất biến** — ghi đè là xoá và ghi lại toàn bộ chunk, không có `UPDATE` lẻ tẻ để lệch.

**`metadata JSONB` thay `heading_path TEXT`** — `metadata` của `Document` là dict, mỗi loader
điền thứ khác nhau:
```
pdf   {"page": 3, "source": "TiendoT6.pdf"}
xlsx  {"sheet": "Hạng mục", "header_row": 2}
txt   {"heading": "## Vướng mắc"}
```
Một cột `TEXT` không diễn đạt được cả ba. `JSONB` thì lọc được `metadata->>'sheet'` khi cần.

**`ON DELETE CASCADE`** — xoá tài liệu thì chunk biến mất theo. Cũng làm nhánh `updated` gọn:
`DELETE FROM doc_chunk WHERE document_id = ?`.

**`VECTOR(768)`** — 768 chiều ứng với `text-embedding-004`. Đổi model là phải đổi số này và
re-embed toàn bộ (đọc lại từ cột `documents`). Số không khớp thì Postgres từ chối insert.

## B4. Index

| index | trên | để làm gì |
|---|---|---|
| `idx_doc_project` | `source_document (project_id, as_of_date DESC)` | Liệt kê tài liệu một dự án, mới nhất trước |
| `idx_chunk_project` | `doc_chunk (project_id)` | Thu hẹp phạm vi trước khi so vector |
| `idx_chunk_vec` | `doc_chunk USING hnsw (embedding vector_cosine_ops)` | Tìm theo ngữ nghĩa |

`vector_cosine_ops` chỉ tăng tốc cho toán tử `<=>`. Truy vấn dùng `<->` hay `<#>` thì Postgres
**bỏ index, quét toàn bảng** — vẫn ra kết quả, chỉ chậm dần theo số dòng cho tới lúc không
dùng được nữa.

`UNIQUE (project_id, file_name)` tự sinh index — đó là thứ `find_by_name` dùng.

## B5. `persistence/proc/`

| file | hàm |
|---|---|
| `projects.py` | `create(name)` · `get(id)` · `list_all()` kèm số tài liệu · `find(fragment)` |
| `documents.py` | **`find_by_name(project_id, file_name)`** · `upsert(...)` · `list_by_project(...)` |
| `chunks.py` | `delete_by_document(id)` · `insert_many(...)` · `search(project_id, embedding, k)` |

`projects.create` dùng `INSERT … ON CONFLICT (normalized_name) DO NOTHING` rồi `SELECT`,
không phải SELECT-rồi-INSERT: hai admin gõ `/duan` cùng lúc là đua nhau.

`chunks.insert_many` dùng `executemany` — một file vài trăm chunk.

## B6. Bảng LangGraph tự tạo

`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` —
`checkpointer.setup()` tự tạo lúc app khởi động. Không viết trong `schema.sql`, không đụng
vào. Scheduler đã có job dọn bản ghi cũ hơn `CHECKPOINT_RETENTION_DAYS` ngày.

---

# THƯ VIỆN

## Đã cài — bản thực tế

| gói | bản | để làm gì |
|---|---|---|
| `langchain-community` | `>=0.3.14,<0.4` (cài 0.3.31) | `TextLoader`, `PyPDFLoader` |
| `langchain-text-splitters` | `>=0.3.5,<0.4` (đã sẵn 0.3.11) | `RecursiveCharacterTextSplitter` |
| `pgvector` | `0.5.0` | Kiểu `VECTOR` cho SQLModel |
| `openpyxl` | `3.1.5` | Đọc `.xlsx` |
| `pypdf` | `6.15.0` | `PyPDFLoader` dùng nó bên dưới |
| `tzdata` | `2026.3` | Windows và Docker slim không có sẵn DB múi giờ IANA |

Embedding **không** cần gói mới — `langchain-google-genai` đã có sẵn
`GoogleGenerativeAIEmbeddings`. Không dùng `unstructured` — xem A5.

Kéo theo: `langchain-community` nâng `pydantic-settings` 2.6.1 → 2.15.0, đã cập nhật pin.

## ⚠ Hai chỗ lệch phát hiện khi kiểm

**1. Pin `langchain-core` quá cũ.** File ghim `==0.3.28` nhưng env chạy **0.3.86** và có sẵn
`langchain 0.3.30`. `pip install -r requirements.txt` sẽ **hạ cấp** rồi kéo theo lỗi ở
`langchain-text-splitters`. Đã nới thành `>=0.3.28,<0.4`.

**2. `text-embedding-004` đã bị Google gỡ** — gọi tới là 404. Danh sách model còn hỗ trợ
`embedContent` giờ chỉ có `gemini-embedding-001`, `gemini-embedding-2-preview`,
`gemini-embedding-2`. Đã đổi sang `gemini-embedding-001`; nó ra 3072 chiều mặc định nhưng
cắt xuống được bằng `output_dimensionality=768`, nên cột `VECTOR(768)` giữ nguyên.

Kèm theo một chi tiết dễ vấp: `aembed_documents` của langchain-core **không nhận**
`output_dimensionality` (bản async chỉ chuyển tiếp `texts`). `shared/embed.py` phải gọi bản
đồng bộ bọc `asyncio.to_thread`.

---

# THỨ TỰ LÀM

| # | làm gì | cần gì |
|---|---|---|
| 0 | `git mv` `app/core/{config,datetime_utils}.py` → `shared/`, sửa import; sửa pin `langchain-core` | — |
| 1 | Sửa `schema.sql`, chạy lên Neon, đối chiếu 3 model SQLModel | Neon + `pgvector` |
| 2 | `persistence/proc/*` + `shared/projects.py` + `utils/writer.py` | như trên |
| 3 | `utils/loaders/*` + `utils/split.py` + `utils/filename.py` + `shared/embed.py` | `langchain-community`, `GOOGLE_API_KEY` |
| 4 | `state.py` + 7 node + `graph.py` | — |
| 5 | Tầng Telegram (`download`, `keyboard`, `render`, `webhooks`) | `.env` hai token |

**Bước 0** trả một khoản nợ: `shared/llm.py` và `persistence/proc/checkpoints.py` đang import
từ `app.core.*`, ngược chiều phụ thuộc đã tuyên bố (`app → graph_* → shared → persistence`).
Cả hai file đó đều thuần, không dính aiogram.

---

# KIỂM CHỨNG

## DB (bước 1–2)

```
psql "$DATABASE_URL" -f persistence/schema/drop_v1.sql    # nếu còn bảng task cũ
psql "$DATABASE_URL" -f persistence/schema/schema.sql
```

| kiểm | kết quả phải có |
|---|---|
| `\dt` + `SELECT extname FROM pg_extension` | 3 bảng, có `vector` |
| Chèn `'Đội xe'` rồi `'ĐỘI XE '` (cùng `normalized_name`) | lần hai **lỗi** unique |
| Cùng `(project_id, file_name)` chèn hai lần | lần hai **lỗi** unique |
| Cùng `file_name`, **khác** `project_id` | cả hai vào được |
| Cùng `content_sha256`, khác tên file, cùng dự án | cả hai vào được ← đã bỏ UNIQUE |
| `DELETE` tài liệu | `count(*)` của `doc_chunk` về 0 |
| `EXPLAIN … ORDER BY embedding <=> …` | thấy `Index Scan`, không phải `Seq Scan` |
| Ép lỗi giữa nhánh ghi đè | chunk cũ **còn nguyên**, `chunk_count` không lệch |

## Hàm thuần (bước 3) — `pytest`, không cần DB lẫn API key

- `filename.base_name`: `"TiendoT6(1).xlsx"` → `"TiendoT6.xlsx"`; `"TiendoT6 (2).xlsx"` →
  `"TiendoT6.xlsx"`; `"Bao cao (ban chinh).pdf"` → **giữ nguyên** (trong ngoặc không phải số).
- `filename.guess_as_of`: `"TiendoT6.xlsx"` → 30/06 năm hiện tại; `"BaoCao_2026-06-30.pdf"`
  → 2026-06-30; `"ghichu.txt"` → `None`.
- `loaders`: file mẫu mỗi định dạng trong `tests/fixtures/`; **xlsx giữ nguyên `datetime` và
  số, không ra serial number của Excel**; pdf điền `metadata["page"]`; đuôi lạ ném
  `UnsupportedFormat`.
- `split`: chunk rỗng bị loại; đoạn dài hơn `chunk_size` bị cắt và có chồng lấn; `metadata`
  của Document gốc được **giữ nguyên** trên mọi chunk con.

## Workflow (bước 4) — `ainvoke` trực tiếp, không cần Telegram

```python
cfg = {"configurable": {"thread_id": "test:1"}}
await graph.ainvoke(
    {"upload": UploadedFile("TiendoT6.xlsx", tmp_path, size),
     "messages": [], "uploaded_by": admin_id}, cfg)

# KHÔNG phải out["__interrupt__"] — khoá đó không tồn tại ở langgraph 0.2.60
assert (await pending_interrupt(graph, cfg))["kind"] == "choose_project"

out = await graph.ainvoke(Command(resume={"project_id": str(pid)}), cfg)
assert out["outcome"] == "created"
```

| kiểm | kết quả phải có |
|---|---|
| Nạp lại **y hệt** file đó | `outcome == "unchanged"`, **không** có interrupt #2, `embed_documents` gọi **0 lần** |
| Nạp `TiendoT6(1).xlsx` nội dung khác | interrupt #2 hiện ra; đồng ý → `"updated"`, tên trong DB vẫn `"TiendoT6.xlsx"`, chunk cũ biến mất sạch |
| Cùng tình huống, bấm **Huỷ** | `outcome == "cancelled"`, `embed_documents` gọi **0 lần**, DB không đổi một dòng |
| Nạp `TiendoT7.xlsx` | `outcome == "created"` — **hai tài liệu cùng sống** |
| Kho chưa có dự án nào | `error: no_projects`, `pending_interrupt` trả `None` |
| PDF chỉ có ảnh scan | `error: empty_document` |
| Xoá file tạm rồi mới resume | `error: temp_file_gone`, không ném traceback |
| Mọi nhánh kết thúc | file tạm **đã bị xoá** |
| `/duan Đội xe` rồi `/duan Đội Xe` | một dòng trong bảng `project` |
