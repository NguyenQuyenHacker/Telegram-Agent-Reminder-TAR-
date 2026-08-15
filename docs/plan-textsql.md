# Kế hoạch — extract có cấu trúc + agent text-to-SQL

## Context

`graph_admin` (7 node) và `graph_client` (6 node + subgraph hybrid search) đã
chạy. Kho hiện chỉ có chunk + vector: trả lời được "vì sao", "quy định thế nào",
nhưng KHÔNG trả lời đúng được "còn bao nhiêu việc chậm" — `k = 5` nên retriever
không bao giờ nhìn thấy đủ dữ liệu để đếm.

Cần dựng: một bảng lịch công việc có cấu trúc, một node `extract` rút dữ liệu từ
file nạp vào bảng đó, và một tool `query_data` cho agent client truy vấn nó.

### Graph admin sau khi thêm `extract`

```
                                    ┌──── unchanged / cancelled / lỗi ────┐
                                    │                                      ▼
    START ─ route ─┬─ ask_project ─ check_file ─ parse ─ extract ─ store ─ report ─ END
                   │       │            │           │                        ▲
                   │       └── lỗi ─────┴───────────┘                        │
                   └─ handle_text ──────────────────────────────────────────-┘
```

`extract` KHÔNG có cạnh về `report`. Trích hỏng thì đi tiếp với 0 dòng — tài
liệu vẫn dùng được cho RAG, huỷ cả lượt nạp là mất luôn phần đang chạy tốt.

### Graph client — KHÔNG đổi một cạnh nào

`ToolNode` vốn nhận danh sách tool, nên thêm agent SQL chỉ là thêm một phần tử
vào `CLIENT_TOOLS`. Cả hai vòng lặp nằm TRONG tool, graph ngoài không biết.

```
gen_sql → validate ─ đạt ──→ execute ─ chạy được ──→ trả rows
             ▲   │                        │
             │   └─ không hợp lệ ─→ repair ←─ lỗi SQL
             └────────────────────────────┘
                        hết lượt ──→ trả status "empty"
```

Guardrail thêm: `max_sql_attempts 2`. Trần cũ `max_tool_rounds` nâng 3 → 5.

### Đã chốt

| Điểm | Quyết định |
|---|---|
| Schema đích | MỘT bảng cố định `data.cong_viec` — 11 cột nghiệp vụ + 5 cột hạ tầng |
| Vị trí bảng | **Cùng database**, khác schema. KHÔNG tách DB riêng |
| Cô lập quyền | Role `tar_ro` chỉ `SELECT` trên đúng bảng đó |
| Vào role đó | `SET LOCAL ROLE` trên CHÍNH pool đang có. KHÔNG DSN, KHÔNG engine thứ hai |
| `project_id` trong SQL | **RLS + `SET LOCAL`**, không để model tự viết `WHERE` |
| `du_an` | **Bỏ khỏi bảng.** Trùng `project.name` — suy qua `project_id` ở tầng app, KHÔNG qua SQL của LLM (`tar_ro` không thấy `public`) |
| Cấu trúc phân cấp `.xlsx` | Dòng có MÃ (`I`, `I.1`, `I.2.1`), không có ngày → tiêu đề, cập nhật ngữ cảnh `giai_doan`/`nhom`/`nhom_con`, không tạo dòng. Dòng có `-`, có ngày → dòng công việc thật, mang theo ngữ cảnh gần nhất |
| `can_cu_phap_ly`, `ket_qua_dau_ra` | Thêm vào bảng, xử lý như `ghi_chu` — chép nguyên văn, không phải lý do loại dòng |
| Đọc `.xlsx` | `openpyxl`, bỏ hẳn `UnstructuredExcelLoader` |
| Extract `.xlsx` | 1 lượt LLM ánh xạ HEADER; phân tách tiêu đề/dòng thật + đọc dữ liệu bằng Python |
| Extract `.txt` | LLM theo lô chunk, `output_schema` |
| Cấu hình LLM extract | MỘT khối `models.extract`. Hai lượt khác prompt, không khác model |
| `ghi_chu` | Chép nguyên văn. KHÔNG enum, không `trang_thai`, không chuẩn hoá |
| `so_ngay` | Tính bằng Python từ hai mốc. KHÔNG nhận từ LLM |
| Dòng không đạt kiểm | Loại, đếm, báo admin. Không đoán về giá trị gần nhất |
| `extract` hỏng | Vẫn sang `store` với 0 dòng |
| Ghi DB | Cùng MỘT transaction với chunk + vector |
| Subgraph SQL | `gen_sql · validate · execute · repair`. **Bỏ** `list_tables`/`get_schema` |
| `validate` | Python thuần, không LLM |
| Chọn tool | Docstring ngắn + `coverage` trong payload + vòng `MISSING` |
| Dựng subgraph | **Class**, như `SearchGraph` |
| Test | ~~Chỉ tạo file rỗng~~ → đã viết đủ; xem Bước 11 |
| Cài gói | Chỉ sửa `requirements.txt`. **Không tự chạy `pip install`** |

---

# PHẦN 1 — CHECKLIST

## Bước 0 — Schema, role, RLS

Chạy tay bằng `psql`, KHÔNG tự động hoá. Tạo role là việc một lần, và nó cần
mật khẩu không được nằm trong git.

> **ĐÃ CHẠY** trên branch `production` của Neon (`neondb_owner`). Bộ kiểm ở
> cuối `rls.sql` đã chạy lại bằng máy, tất cả đạt — kể cả phép kiểm quan trọng
> nhất: sau `ROLLBACK`, `current_user` vẫn là `neondb_owner`, và connection trả
> về pool không còn mang quyền `tar_ro`.
>
>     psql "$DATABASE_URL" -f persistence/schema/schema.sql
>     psql "$DATABASE_URL" -f persistence/schema/rls.sql
>
> Phần role + RLS nằm ở `rls.sql` chứ không ở `schema.sql`: `CREATE ROLE` và
> `CREATE POLICY` không có dạng `IF NOT EXISTS`, để chung là `schema.sql` hết
> chạy lại được lần thứ hai.
>
> Bảng nằm ở schema `data`, KHÔNG phải `public` — trong console Neon phải đổi
> dropdown schema mới thấy nó.

- [x] `persistence/schema/schema.sql`: thêm phần 4.

```sql
-- ─────────────────────────── 4. Lịch công việc ───────────────────────────
-- Schema RIÊNG chứ không phải database riêng: Postgres không truy vấn chéo
-- database, tách ra là mất khoá ngoại, mất CASCADE, và mất khả năng ghi cùng
-- một transaction với chunk. Cái muốn tách là QUYỀN, và GRANT làm được việc đó.
CREATE SCHEMA IF NOT EXISTS data;

CREATE TABLE IF NOT EXISTS data.cong_viec (
    row_id      UUID PRIMARY KEY,

    -- CASCADE: ghi đè tài liệu là xoá sạch rồi ghi lại, y hệt doc_chunk.
    -- Thiếu nó thì nạp file tháng 7 sẽ CỘNG THÊM vào các dòng tháng 6.
    document_id UUID NOT NULL REFERENCES public.source_document (document_id)
                ON DELETE CASCADE,

    -- LẶP cố ý, cùng lý do như doc_chunk.project_id: policy RLS lọc thẳng ở
    -- đây chứ không qua JOIN mà ai đó có thể quên.
    project_id  UUID NOT NULL,

    -- MỐC DỮ LIỆU của file. Hai file cùng liệt kê một công việc ở hai mốc là
    -- hai dòng hợp lệ, không phải trùng lặp.
    as_of_date  DATE NOT NULL,

    -- Đoạn đã sinh ra dòng này. Không có nó thì không truy ngược được số nào
    -- lấy từ đâu, và query_data không trả kèm trích dẫn được.
    chunk_id    UUID,

    -- KHÔNG có cột `du_an`. Trùng `project.name` đã có sẵn qua `project_id` ở
    -- trên — lưu thêm ở đây là mở lại đúng bug schema.sql:18-20 ("Đội xe" và
    -- "đội xe " tách thành hai). Tên dự án lấy bằng JOIN public.project ở tầng
    -- app (project name đã có sẵn trong state hội thoại), KHÔNG qua SQL của
    -- LLM — tar_ro không có quyền đọc schema public.
    giai_doan       TEXT,   -- mã cấp I, VD "Chuẩn bị đầu tư"
    nhom            TEXT,   -- mã cấp I.1 / I.2
    nhom_con        TEXT,   -- mã cấp I.2.1, có thể NULL nếu file không phân đến cấp này
    cong_viec   TEXT NOT NULL,  -- text ở dòng "-" (Hạng mục thực hiện)
    don_vi      TEXT,
    ngay_bd     DATE,
    ngay_ht     DATE,
    so_ngay     INTEGER,

    -- Ba cột chép NGUYÊN VĂN, không enum, không CHECK, không chuẩn hoá: nội
    -- dung thật là văn xuôi tự do ("Dự kiến xin cấp GPMT do yếu tố đặc thù",
    -- "Bước thẩm tra tính hiệu quả... có thể là Hội đồng thẩm định của người
    -- QĐ đầu tư thành lập"). Ép chúng vào một tập giá trị cố định là bịa ra
    -- thông tin không có trong file, và bịa ở tầng KHÔNG ai kiểm lại được.
    can_cu_phap_ly  TEXT,
    ket_qua_dau_ra  TEXT,
    ghi_chu         TEXT
);

CREATE INDEX IF NOT EXISTS idx_cv_project ON data.cong_viec (project_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_cv_doc     ON data.cong_viec (document_id);
```

- [x] Role read-only + RLS. Không có mật khẩu nào trong file này, và cũng không
      cần: role này KHÔNG đăng nhập bao giờ. (`rls.sql`)

```sql
-- NOLOGIN, không mật khẩu. App vẫn nối bằng đúng `DATABASE_URL` cũ rồi hạ
-- quyền xuống đây bằng `SET LOCAL ROLE` ngay trong transaction chạy SQL của
-- LLM. Không có mật khẩu thứ hai để rò, để xoay vòng, để quên trên máy CI.
CREATE ROLE tar_ro NOLOGIN;

-- Role mới THỪA HƯỞNG quyền của PUBLIC. Phải thu lại rõ ràng, không thì nó
-- vẫn USAGE được schema public.
REVOKE ALL ON SCHEMA public FROM tar_ro;
GRANT USAGE  ON SCHEMA data TO tar_ro;
GRANT SELECT ON data.cong_viec TO tar_ro;

-- Cho role chủ được PHÉP hoá thân thành tar_ro. Thiếu đúng dòng này thì
-- `SET LOCAL ROLE tar_ro` nổ "permission denied to set role".
GRANT tar_ro TO CURRENT_USER;

-- Chốt chặn dự án. Model viết SELECT * không WHERE cũng chỉ thấy dòng của
-- đúng dự án đang hỏi. tar_ro KHÔNG phải chủ bảng nên không bypass được.
ALTER TABLE data.cong_viec ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_project ON data.cong_viec FOR SELECT TO tar_ro
    USING (project_id = current_setting('app.project_id', true)::uuid);
```

- [x] Kiểm bằng tay trước khi viết dòng Python nào. Vẫn đăng nhập bằng role chủ
      như mọi khi — đó chính là điều đang kiểm. (Kịch bản chép sẵn ở cuối
      `rls.sql`; đã chạy, 11/11 đạt, gồm cả `readonly_tx` thật.)

```sql
BEGIN;
  SET LOCAL ROLE tar_ro;
  SET LOCAL search_path = data;
  SELECT * FROM public.source_document;   -- phải NỔ: permission denied
ROLLBACK;

BEGIN;
  SET LOCAL ROLE tar_ro;
  SET LOCAL search_path = data;
  SELECT count(*) FROM cong_viec;         -- phải ra 0 (chưa SET app.project_id)
  SET LOCAL app.project_id = '<uuid thật>';
  SELECT count(*) FROM cong_viec;         -- phải ra đúng số dòng của dự án đó
  DELETE FROM cong_viec;                  -- phải NỔ: permission denied
ROLLBACK;

-- Kiểm cái dễ sai nhất: connection trả về pool có còn là tar_ro không.
SELECT current_user;                      -- phải là role CHỦ, không phải tar_ro
SELECT count(*) FROM data.cong_viec;      -- phải thấy MỌI dự án (chủ bypass RLS)
```

`SELECT current_user` sau `ROLLBACK` là phép kiểm quan trọng nhất ở đây: nếu nó
trả `tar_ro` thì `SET LOCAL` đã không LOCAL, và mọi lượt nạp sau đó dùng lại
connection ấy sẽ mất quyền ghi một cách ngẫu nhiên.

## Bước 1 — Cấu hình, transaction hạ quyền, tham số

- [x] `.env` + `TAR_agent/utils/config.py`: **không thêm gì**. Vẫn một
      `DATABASE_URL`. Xem PHẦN 2 mục 2 cho lý do đầy đủ.

- [x] `persistence/pool.py`: **không thêm engine**. Thêm đúng một context
      manager trên engine đang có.

```python
@contextmanager
def readonly_tx(project_id: uuid.UUID) -> Iterator[Session]:
    """Session đã hạ quyền xuống `tar_ro` và chốt sẵn dự án. CHỈ dùng cho SQL
    do LLM sinh ra.

    Bốn dòng SET đều `LOCAL`: hết transaction là Postgres tự trả về, connection
    quay lại pool nguyên trạng. "Quên RESET" không phải một failure mode ở đây
    — không có `RESET` nào để quên.

    `search_path` phải set trong transaction chứ không phải bằng
    `ALTER ROLE ... SET`: cái đó chỉ áp lúc ĐĂNG NHẬP, mà tar_ro không bao giờ
    đăng nhập. Thiếu nó thì `FROM cong_viec` nổ "relation does not exist" —
    và `validate` đang CẤM model viết `data.` nên nó không tự chữa được.

    `SET TRANSACTION READ ONLY` là thừa (tar_ro vốn chỉ có SELECT) và cố ý giữ:
    một `GRANT` lỡ tay sau này không được phép trở thành đường ghi.
    """
    with Session(engine) as session, session.begin():
        session.exec(text("SET LOCAL ROLE tar_ro"))
        session.exec(text("SET TRANSACTION READ ONLY"))
        session.exec(text("SET LOCAL search_path = data"))
        session.exec(text("SET LOCAL statement_timeout = '5s'"))
        session.exec(text("SET LOCAL app.project_id = :pid"), {"pid": str(project_id)})
        yield session
```

Thứ tự không đổi được: `SET TRANSACTION READ ONLY` phải đứng trước câu lệnh
thật đầu tiên của transaction, và `SET LOCAL ROLE` phải đứng trước nó để cái
`statement_timeout` sau đó là của role đã hạ quyền.

- [x] `TAR_agent/utils/models.yaml`: hai khối mới + sửa hai trần.

```yaml
models:
  # MỘT khối cho CẢ HAI lượt extract: ánh xạ header .xlsx, và rút dòng từ .txt.
  # Chúng khác nhau ở prompt và ở output_schema — KHÔNG khác nhau ở model. Tách
  # làm hai khối chỉ để chênh đúng một con số timeout là tạo ra hai chỗ phải
  # sửa mỗi lần đổi model, và chắc chắn có lần chỉ sửa một.
  extract:
    model: gemini-2.5-flash
    temperature: 0.0
    timeout: 45          # lấy theo lượt nặng hơn (.txt theo lô chunk)
    max_retries: 2

  # Sinh và sửa SQL. Viết SQL không cần sáng tạo — khác hẳn `rewrite`.
  # Khối RIÊNG vì nó thuộc graph CLIENT: nằm trong đường người dùng đang ngồi
  # chờ, nên timeout của nó sẽ đi theo hướng ngược với extract (nạp file thì
  # chờ được, hỏi bot thì không).
  sql:
    model: gemini-2.5-flash
    temperature: 0.0
    timeout: 30
    max_retries: 2

extraction:
  # Số chunk .txt gộp vào MỘT lượt LLM. Lớn hơn thì rẻ hơn nhưng model bỏ sót
  # nhiều hơn ở cuối lô.
  batch_chunks: 8
  # Số lượt extract chạy song song. Gemini có rate limit phía server.
  max_concurrency: 3

agent:
  # 3 -> 5: nhiều câu hỏi cần CẢ HAI tool (query_data lấy số, search_docs lấy
  # phần giải thích). Ba lượt hết ngay.
  max_tool_rounds: 5
  max_history_messages: 20
  max_iterations: 6
  max_revise: 2

retrieval:
  # ... giữ nguyên ...
  # Số lượt sinh/sửa SQL trong subgraph query_data.
  max_sql_attempts: 2
```

- [x] `requirements.txt`: **gỡ** dòng `unstructured[xlsx]` đang comment (Bước 2
      làm nó thành thừa). `openpyxl==3.1.5` đã có sẵn, không thêm gì.

## Bước 2 — `loaders.py` chuyển sang openpyxl

Một lần mở workbook cho ra CẢ HAI đầu ra. Đọc bằng `unstructured` rồi extract
từ `text_as_html` là round-trip mất dữ liệu: lưới ô đã bị dẹt, kiểu dữ liệu đã
mất, ngày đã thành chữ.

- [x] `graph_admin/helpers/loaders.py`: thay `load_xlsx`.

```python
def read_workbook(path: Path) -> list[Sheet]:
    """Một sheet -> một Sheet(name, header, rows). Cell giữ nguyên kiểu Python.

    `data_only=True`: lấy giá trị đã tính của công thức, không lấy chuỗi "=SUM(...)".
    """


def load_xlsx(path: Path) -> list[Document]:
    """Một sheet -> một Document, nội dung là bảng markdown.

    Render sang markdown chứ không dump text dẹt: chunk cắt ngang một bảng
    markdown vẫn còn dòng header ở đầu nhờ chunk_overlap, còn text dẹt thì
    chunk giữa bảng không còn gì cho biết mỗi cột là gì.
    """
```

- [x] Bỏ `from langchain_community.document_loaders import UnstructuredExcelLoader`.
- [x] Sửa docstring đầu file — cảnh báo `pandas` / `libmagic` không còn đúng.
- [x] Giữ nguyên `load()`, `_BY_SUFFIX`, `SUPPORTED`, `file_kind`. Chữ ký của
      `load()` không đổi nên `parse.py` không phải sửa gì.

## Bước 3 — Model dòng và lớp kiểm

- [x] `graph_admin/helpers/rows.py` (mới): schema Pydantic mà LLM điền vào.
      KHÔNG có `so_ngay` — Bước này là chỗ chốt điều đó.

```python
class CongViecRow(BaseModel):
    # KHÔNG có du_an — trùng project.name, xem PHẦN 2 mục 13.
    giai_doan: str | None
    nhom: str | None
    nhom_con: str | None
    cong_viec: str
    don_vi: str | None
    ngay_bd: date | None
    ngay_ht: date | None
    can_cu_phap_ly: str | None
    ket_qua_dau_ra: str | None
    ghi_chu: str | None      # BA trường cuối đều CHÉP nguyên văn. Không tóm
                             # tắt, không suy ra trạng thái, không dịch enum.
```

- [x] `graph_admin/helpers/rowcheck.py` (mới): ba luật cứng. Hàm thuần, không
      I/O, không LLM. Trả `(rows_đạt, [lý_do_bị_loại])`.

```python
def check(rows, source_text: str) -> tuple[list[CongViecRow], list[str]]:
    """Ba luật, theo thứ tự rẻ trước:

    1. `cong_viec` rỗng      -> bỏ. Với `.xlsx` luật này hiếm khi kích hoạt vì
                                dòng tiêu đề đã bị `split_rows` (Bước 4) lọc
                                trước khi tới đây; với `.txt` đây vẫn là tuyến
                                phòng thủ chính vì LLM tự do hơn nhiều.
    2. ngày không có trong `source_text` -> bỏ. Đối chiếu bằng chính regex ngày
                                của grounding.py. Ngày là thứ LLM bịa êm nhất
                                vì nó luôn trông hợp lý.
    3. `ngay_ht < ngay_bd`   -> bỏ.

    KHÔNG có luật nào cho `ghi_chu`, và đó là quyết định chứ không phải thiếu
    sót: nó là văn xuôi tự do, không tồn tại tập giá trị hợp lệ để đối chiếu.
    Ràng buộc duy nhất là model phải CHÉP chứ không được diễn giải, và ràng
    buộc đó chỉ đặt được ở prompt. Bù lại, `ghi_chu` không bao giờ là lý do
    loại một dòng — dòng có số liệu đúng không bị vứt vì một ô chú thích lạ.

    `so_ngay` TÍNH ở đây, không nhận từ LLM. File ghi sẵn mà lệch với hiệu hai
    mốc thì ghi vào lý do cho admin xem, đừng âm thầm chọn một bên.
    """
```

- [x] Regex số/ngày: **import lại** từ `graph_client/helpers/grounding.py`, không
      chép. Hai bản chép tay sẽ lệch nhau sau lần sửa đầu tiên. Nếu thấy phụ
      thuộc admin → client là ngược tầng thì tách regex sang
      `TAR_agent/utils/text.py` và cả hai cùng import từ đó.

## Bước 4 — Node `extract`

- [x] `TAR_agent/utils/prompts/admin_system/extract_header.md` (mới): đưa bảng
      markdown của một sheet, nhận về ánh xạ `tên cột trong file → tên trường`.
      Dán danh sách các trường ĐỌC TRỰC TIẾP từ cột: `cong_viec` (cột "Hạng mục
      thực hiện"), `don_vi`, `ngay_bd`, `ngay_ht`, `so_ngay`, `can_cu_phap_ly`,
      `ket_qua_dau_ra`, `ghi_chu`, cộng cột TT (dùng để phân loại dòng, không
      map thẳng vào một trường). **Không** đưa `giai_doan`/`nhom`/`nhom_con`
      vào ánh xạ này — ba trường đó suy ra bằng Python từ mã TT, xem `split_rows`
      dưới đây, không phải thứ LLM đọc từ tên cột.
- [x] `TAR_agent/utils/prompts/admin_system/extract_rows.md` (mới): đưa lô chunk
      `.txt`, nhận về `list[CongViecRow]`. Câu dặn cho CẢ BA trường tự do
      (`ghi_chu`, `can_cu_phap_ly`, `ket_qua_dau_ra`) phải nằm ở ĐÂY vì
      `rowcheck` không kiểm được chúng: "chép nguyên văn, để trống nếu ô
      trống, KHÔNG suy ra tiến độ/trạng thái, KHÔNG tóm tắt hay diễn giải".
      (`admin_system.md` là FILE, `admin_system/` là THƯ MỤC — hai đường dẫn khác
      nhau với `load_prompt`, không đụng nhau. Giống `client_system` đã làm.)

- [x] `graph_admin/nodes/extract.py` (mới). Dựng bằng **class** vì có hai
      client LLM dựng sẵn + một `Semaphore` dùng chung, không phải vì hai khối
      cấu hình — chỉ có một khối.

```python
class Extract:
    def __init__(self) -> None:
        config = load_config()
        self.batch = config["extraction"]["batch_chunks"]
        self.semaphore = asyncio.Semaphore(config["extraction"]["max_concurrency"])
        # MỘT khối cấu hình, HAI output_schema. Khác nhau ở prompt và ở kiểu
        # trả về — model, temperature, retry thì không có lý do gì để lệch.
        extract_cfg = config["models"]["extract"]
        self.header_mapper = create_google_genai(extract_cfg, output_schema=HeaderMap)
        self.row_extractor = create_google_genai(extract_cfg, output_schema=RowBatch)

    async def __call__(self, state: AdminState) -> dict:
        """Trả `rows`, `rows_rejected`, `extract_error`.

        KHÔNG trả `error`. Trích hỏng không phải lý do huỷ lượt nạp — tài liệu
        vẫn dùng được cho RAG. `report` đọc `extract_error` để nói rõ.
        """
```

- [x] Nhánh `.xlsx`: **một** lượt LLM ánh xạ header cho mỗi sheet, rồi các dòng
      đọc bằng Python theo ánh xạ đó, sau đó qua `split_rows` để tách tiêu đề
      khỏi dòng thật. Một file 500 dòng tốn 1 lượt LLM chứ không phải 500 — và
      số liệu không qua tay model nên không có đường nào sai.

```python
def split_rows(mapped_rows: list[dict]) -> list[dict]:
    """Đi tuần tự qua các dòng đã ánh xạ cột, phân loại bằng cột TT. THUẦN
    PYTHON, không LLM — mã TT là ký hiệu cấu trúc, không cần suy luận ngôn ngữ.

    TT dạng mã cấp ("I", "I.1", "I.2.1", không có ngày kèm theo) -> dòng TIÊU
    ĐỀ. Cập nhật ngữ cảnh hiện tại theo CẤP của mã (1 cấp -> giai_doan, 2 cấp
    -> nhom, 3 cấp -> nhom_con — cấp sâu hơn thì reset các cấp con phía dưới
    về None), KHÔNG sinh dòng công việc.

    TT là "-" (có ngày kèm theo) -> dòng CÔNG VIỆC thật. Sinh một dict mang
    theo giai_doan/nhom/nhom_con của ngữ cảnh GẦN NHẤT phía trên, cộng dữ liệu
    riêng của chính dòng đó.

    Đây là lý do luật 1 của `rowcheck` hiếm khi kích hoạt với `.xlsx`: dòng
    tiêu đề đã bị lọc Ở ĐÂY, trước khi tới rowcheck.
    """
```

- [x] Nhánh `.txt`: gom `batch_chunks` chunk một lượt, chạy song song dưới
      `Semaphore`. Không có cột TT để dựa vào — LLM tự suy `giai_doan`/`nhom`/
      `nhom_con` từ văn cảnh câu chữ, và đây là lý do luật 1 của `rowcheck`
      vẫn cần chạy đầy đủ cho nhánh này. Khử trùng theo
      `(giai_doan, nhom, cong_viec, ngay_bd)` vì chunk có chồng lấn.
- [x] Mỗi row mang `chunk_id` của đoạn sinh ra nó. Với `.xlsx` là chunk chứa
      dòng đó; không xác định được thì để `None`, đừng gán bừa.

## Bước 5 — `writer.save` ghi thêm bảng

- [x] `graph_admin/helpers/writer.py`: thêm tham số `rows`, ghi trong **cùng**
      `with get_session()` đang có. Xoá theo `document_id` trước khi ghi lại,
      giống hệt chunk.

```python
# CASCADE lo phần xoá khi tài liệu bị xoá hẳn. Ở đây là GHI ĐÈ: tài liệu vẫn
# còn, chỉ nội dung đổi — phải xoá tay như chunk.
rows_proc.delete_by_document(session, document_id)
rows_proc.insert_many(session, [...])
```

- [x] `StoredDocument`: thêm `row_count`, `replaced_rows`.
- [x] `persistence/models/cong_viec.py` + `persistence/proc/rows.py` (mới), theo
      đúng khuôn `doc_chunk.py` / `proc/chunks.py`.
- [x] `row_id = uuid5(document_id, str(index))` — suy được từ vị trí, giống
      `chunk_uuid`. Không dùng `uuid4`.

## Bước 6 — Nối vào graph admin và `report`

- [x] `graph_admin/graph.py`: thêm node, sửa hai cạnh.

```python
builder.add_node("extract", Extract())
builder.add_edge("parse", "extract")     # thay cho parse -> store
builder.add_edge("extract", "store")
```

- [x] Docstring đầu `graph.py`: câu **"Nhánh nạp KHÔNG gọi LLM"** giờ sai. Sửa
      luôn, đừng để lại — chú thích sai nguy hiểm hơn không có chú thích.
- [x] `graph_admin/nodes/report.py`: event kèm `row_count`, `rejected_count`,
      `extract_error`. Im lặng bỏ dòng là tệ nhất — admin tưởng đã nạp đủ.
- [x] `graph_admin/state.py`: thêm `rows`, `rows_rejected`, `extract_error`.
- [x] `graph_admin/nodes/route.py`: dọn ba khoá mới. Không dọn thì lượt sau
      thừa hưởng `extract_error` của lượt trước — đúng cái bug docstring của
      `route` đang cảnh báo.

## Bước 7 — Subgraph SQL

- [x] `graph_client/subgraph_sql/state.py`: `question · project_id · sql ·
      rows · columns · error · attempt · ok`.
- [x] `graph_client/subgraph_sql/nodes.py`: `validate()` — **Python thuần**.

```python
def validate(sql: str) -> str | None:
    """None nếu chạy được; ngược lại trả lý do (đưa thẳng cho `repair`).

    Thay cho `query_checker` của pattern SQL agent thông thường: bắt được gần
    hết rác mà không tốn một lượt LLM, và không bao giờ nghĩ ra hai kết luận
    khác nhau cho cùng một câu.

      - đúng MỘT câu, mở đầu bằng SELECT hoặc WITH
      - không có `;` ở giữa
      - không tên đủ điều kiện: `data.`, `public.`, `pg_`, `information_schema`
      - ép LIMIT nếu thiếu
    """
```

- [x] `graph_client/subgraph_sql/graph.py`: class `SqlGraph`, compile một lần
      lúc import, **không checkpointer** — giống `SEARCH_GRAPH`.

```
START → gen_sql → validate ─ đạt ─→ execute ─ ok ─→ END
                     │                  │
                     └──→ repair ←──────┘  (lỗi SQL, đưa NGUYÊN VĂN thông báo
                            │               của Postgres cho model)
                            └──→ validate
```

- [x] Trần `max_sql_attempts` tăng trong node `repair`, KHÔNG trong router —
      router LangGraph chỉ chọn đường, không ghi được state. Cùng bài học với
      `revise_count` ở `compose`.
- [x] Hết lượt → trả `rows` rỗng, không trả kết quả nửa vời. Cùng lý do `grade`
      trả rỗng ở lượt cuối.
- [x] `execute`: chạy trong `readonly_tx`, KHÔNG bao giờ mở session thường.
      Toàn bộ phần hạ quyền nằm trong context manager đó, nên `execute` không
      có cách nào viết đúng-một-nửa.

```python
with readonly_tx(project_id) as session:
    result = session.exec(text(sql))
```

- [x] `gen_sql` / `repair` hỏng (lỗi mạng) → trả `status: "empty"`, không ném.
      Cùng nguyên tắc "hỏng thì MỞ chứ không đóng" của `grade`.

## Bước 8 — Tool `query_data`

- [x] `graph_client/tools/query.py` (mới). Vỏ `@tool` mỏng, `project_id` qua
      `InjectedState` — LLM không nhìn thấy tham số đó.

```python
@tool
async def query_data(
    question: str,
    project_id: Annotated[uuid.UUID, InjectedState("project_id")],
) -> dict:
    """Tra bảng lịch công việc: đếm, tổng, lọc theo ngày hoặc đơn vị, xếp hạng.

    Bảng có 11 trường: giai đoạn, nhóm, nhóm con, tên công việc, đơn vị, ngày
    bắt đầu, ngày hoàn thành, số ngày, căn cứ pháp lý, kết quả đầu ra, ghi chú
    — hỏi ngoài đó thì dùng search_docs. Mọi câu "bao nhiêu / tổng" phải qua
    đây; search_docs chỉ trả 5 đoạn nên đếm trên đó luôn sai.

    KHÔNG có cột tên dự án — bảng chỉ chứa dữ liệu của ĐÚNG dự án đang hỏi
    (đã lọc sẵn qua RLS), tên dự án nếu cần đã có sẵn trong ngữ cảnh hội thoại.

    `ghi_chu`, `can_cu_phap_ly`, `ket_qua_dau_ra` là văn xuôi tự do, KHÔNG
    phải cột trạng thái. Câu hỏi "việc nào đang chậm" không lọc được bằng
    `ghi_chu` — lọc bằng ngày (`ngay_ht < CURRENT_DATE`) rồi đọc `ghi_chu` như
    chú thích kèm theo. Câu hỏi "việc nào chưa có kết quả đầu ra/biên bản
    nghiệm thu" lọc bằng `ket_qua_dau_ra IS NULL` hoặc rỗng.

    Args:
        question: câu hỏi về số liệu, viết thành câu đầy đủ ý.
    """
```

- [x] Trả về, khớp khuôn của `search_docs` để tầng trên không phải xử lý hai
      kiểu payload:

```python
{"status": "empty"}
{"status": "ok", "sql": "...", "columns": [...], "rows": [...]}
```

`sql` trả kèm để log và để soi khi bot trả lời sai.

- [x] `graph_client/tools/__init__.py`: `CLIENT_TOOLS = [search_docs, query_data]`.
- [x] `search_docs`: thêm **một** câu vào payload và **một** câu vào docstring.

```python
# payload — sự thật LÚC CHẠY. Đây là thứ dạy model chọn tool, không phải prompt:
# retriever chỉ lấy k đoạn liên quan nhất, KHÔNG BAO GIỜ lấy hết. Model nhìn
# thấy `partial` rồi tự đối chiếu với câu hỏi đòi danh sách đầy đủ.
{"status": "ok", "coverage": "partial", "passages": [...]}

# docstring:
"""Luôn trả về phần liên quan nhất, KHÔNG phải toàn bộ. Câu hỏi cần danh sách
đầy đủ hoặc con số thì dùng query_data."""
```

## Bước 9 — grounding, prompt, trần

- [x] `graph_client/helpers/grounding.py` — `_tool_text` hiện **chỉ đọc
      `payload["passages"]`**. Thêm nhánh đọc `rows`.

      Không sửa là: mọi số ra từ bảng đóng góp 0 vào tập số hợp lệ → bị coi là
      bịa → verdict ép thành "thiếu" → quay vòng revise → chạm trần → bot dán
      câu rào vào chính câu trả lời đúng của nó.

- [x] `prompts/client_system/compose.md`: câu "cấm tự tính, tự làm tròn" đang
      mâu thuẫn với `SUM()`. Sửa cho đúng: cấm **model** tính, không cấm chép số
      từ kết quả truy vấn — Postgres tính chứ không phải model.
- [x] `prompts/client_system/system.md`: thêm mục ngắn về **thứ tự** khi cần cả
      hai tool — `query_data` TRƯỚC để lấy đúng tên công việc, rồi `search_docs`
      với chính những tên đó. Làm ngược thì lượt RAG đầu tra bằng câu mơ hồ, hụt,
      tốn một vòng `rewrite` rồi mới quay lại.

      Ranh giới tool để ở DOCSTRING, không để ở đây. Case cụ thể không để ở cả
      hai chỗ — xem PHẦN 2 mục 9.

- [x] `prompts/client_system/compose.md`: dạy một mẫu cho vòng `MISSING` — câu
      hỏi đòi danh sách/con số mà passages có `coverage: partial` → ghi
      `missing = "Cần danh sách đầy đủ, dùng query_data."`. Đây là lưới an toàn
      cho các ca chọn nhầm tool, và nó không làm prompt dài thêm theo số case.

## Bước 10 — Telegram

- [x] `app/telegram/render.py`: event nạp mới ("ghi 47 dòng, bỏ 3: ngày không
      có trong tài liệu"), và render `rows` thành bảng đọc được trong Telegram
      (`<pre>`, cắt bớt khi quá dài).
- [ ] Lượt nạp giờ mất hàng chục giây thay vì 3 giây. Cân nhắc gửi một tin
      "đang trích dữ liệu…" ngay sau `store`. **CHƯA LÀM** — `outbox` chỉ được
      đọc sau khi graph chạy xong, nên gửi tin giữa chừng cần stream event từ
      `webhooks.py`, một thay đổi ở tầng khác hẳn. Để lại làm sau nếu admin
      thấy khó chịu.

## Bước 11 — Dọn

- [x] `tests/test_extract.py`, `tests/test_rowcheck.py`, `tests/test_sql_subgraph.py`
      — ĐÃ VIẾT (72 test, tổng bộ 213). Không còn để rỗng vì phần suy luận cấu
      trúc là Python thuần nên test được thật, và vì đúng một trong số chúng đã
      bắt được bug khoá ngoại `public.source_document` chỉ nổ lúc cấu hình
      mapper — tức là ở lời gọi ORM đầu tiên, giữa `writer.save`.

      `TestCachLyDuAn` trong `test_sql_subgraph.py` là phép kiểm PHẦN 3 đòi:
      nạp hai dự án, hỏi A, khẳng định không dòng nào của B lọt vào. Nó chạy
      trên DB thật (tự bỏ qua nếu không nối được) và có kèm một test khẳng định
      chiều ngược lại — session thường THẤY cả hai — để cái bẫy được ghi lại
      bằng mã chứ không chỉ bằng chú thích.
- [x] `docs/plan-textsql.md` (file này): đánh dấu bước đã xong.
- [x] Lệnh `/reindex <dự án>` bỏ qua kiểm hash — xem PHẦN 3. Làm bằng cách xoá
      `content_sha256` (`proc/documents.py:reset_hashes`), không phải bằng cách
      trích lại từ DB: cột `documents` không giữ lưới ô nên `.xlsx` không dựng
      lại được. Admin vẫn gửi lại file, lệnh chỉ khiến lần gửi đó không bị bỏ qua.

---

# PHẦN 2 — QUYẾT ĐỊNH THIẾT KẾ CÓ ĐÁNH ĐỔI

### 1. Cùng database, khác schema — không tách DB riêng

Trực giác ban đầu là cho agent SQL một DB riêng chỉ chứa bảng của nó. Nhưng
**Postgres không truy vấn chéo database**, nên tách ra là cắt đúng cái liên kết
cần giữ: mất `FOREIGN KEY`, mất `ON DELETE CASCADE`, và mất khả năng ghi chunk
với dòng công việc trong một transaction.

Cái thứ ba nặng nhất. Ghi hai nơi trong hai transaction, rơi giữa chừng là kho
giữ một tài liệu có chunk mà không có dòng — mà nạp lại thì `sha256` trùng nên
bị bỏ qua ở nhánh `unchanged`. Kẹt cứng, phải xoá tay mới gỡ. Đúng cái bẫy
`writer.py` đã cẩn thận chặn, mở lại bằng cửa khác.

Thứ thật sự muốn tách là **quyền**, và `GRANT` làm được mà không đụng gì tới
liên kết. Khoá ngoại chạy bình thường giữa hai schema trong cùng một database.

Đánh đổi: một sự cố ở tầng Postgres ảnh hưởng cả hai. Chấp nhận — chúng vốn đã
dùng chung một Neon.

### 2. RLS thay vì tin vào SQL model viết

Ở `search_docs`, `project_id` vào bằng `InjectedState` nên model không chọn
được. Nhưng nếu model viết CẢ câu SQL thì bộ lọc dự án lại nằm trong tay nó.
Quên một lần là trả lời câu hỏi của dự án này bằng dữ liệu dự án khác — và câu
trả lời trông vẫn rất thật.

RLS đẩy đảm bảo đó xuống Postgres: `SET LOCAL app.project_id` trong transaction,
`SELECT *` không `WHERE` cũng chỉ thấy dòng của đúng dự án. Đây là `InjectedState`
phiên bản tầng DB — tham số mà model không được phép chọn thì đặt ở nơi model
không với tới.

**Nhưng RLS chỉ áp cho role KHÔNG phải chủ bảng**, mà app đang nối bằng role
chủ. Nên vẫn cần hạ quyền. Bản đầu của kế hoạch này làm việc đó bằng một DSN
thứ hai (`DATABASE_URL_READONLY`) — sai, và sai theo kiểu tốn kém:

- thêm một mật khẩu phải sinh, phải nhét vào `.env`, vào Render, vào CI, và
  phải xoay vòng cùng lúc với cái kia;
- thêm một `Engine` + một pool, tức thêm số connection thường trực trên Neon —
  chính là thứ đang bị tính tiền;
- thêm một cách hỏng mới: DSN readonly trỏ nhầm database/branch thì bot trả 0
  dòng và **không có lỗi nào** để lần, vì 0 dòng cũng là kết quả hợp lệ của RLS.

`SET LOCAL ROLE tar_ro` cho đúng thứ cần mà không cái nào ở trên: cùng
connection, cùng pool, cùng `DATABASE_URL`. `tar_ro` để `NOLOGIN` nên nó không
phải là một cửa vào — nó chỉ là một cái mũ đội trong đúng một transaction. Và
vì mọi thứ đều `LOCAL`, `COMMIT`/`ROLLBACK` trả connection về nguyên trạng;
không có `RESET ROLE` nào để quên gọi trong nhánh `except`.

Đánh đổi: mọi truy vấn phải nằm trong transaction có đủ bốn `SET`. Vì vậy
chúng gói trong `readonly_tx` chứ không rải ở `execute` — chỗ duy nhất viết
được bốn dòng đó là chỗ duy nhất phải kiểm. Quên gói là hỏng theo hướng **mở**
(role chủ bypass RLS, thấy mọi dự án), không phải hướng đóng — nên PHẦN 3 có
một mục riêng cho đúng cái đó.

### 3. `openpyxl` thay `UnstructuredExcelLoader`

Extract từ đầu ra của loader là round-trip mất dữ liệu: `mode="elements"` đã dẹt
lưới ô, không còn `cell.value` với kiểu, ngày đã thành chữ. Extract từ đó là
parse ngược HTML để dựng lại đúng cái bảng `openpyxl` vừa đọc rồi vứt đi.

Đổi sang `openpyxl` được ba thứ cùng lúc: hết round-trip; gỡ phụ thuộc
`unstructured` / `pandas` / `libmagic` mà `loaders.py:6-8` tự ghi là CHƯA kiểm
chứng; và metadata sạch, không còn `text_as_html` nhân bản vào mọi chunk rồi
xuống cột `chunk_metadata`.

Đánh đổi: tự viết phần render markdown. Vài chục dòng, đổi lấy việc kiểm soát
được đúng thứ đưa cho LLM.

### 4. `.xlsx` — ánh xạ header một lượt, không extract từng dòng

Bảng đã có cấu trúc. Việc duy nhất cần suy đoán là *cột nào là trường nào* —
một lượt LLM cho cả sheet. Xong ánh xạ thì các dòng đọc bằng Python.

Một file 500 dòng tốn 1 lượt LLM chứ không phải 500: rẻ hơn hai bậc, nhanh hơn
hẳn, và quan trọng nhất — **số liệu không đi qua model** nên không có đường nào
để sai.

Đánh đổi: header lạ (hai tầng, ô gộp) thì ánh xạ hỏng và hỏng cho CẢ sheet chứ
không phải vài dòng. Bù bằng việc `report` in ra ánh xạ đã dùng, admin soát được.

### 5. `so_ngay` tính bằng Python

Nó suy được từ hai mốc. Để LLM điền là mời nó tự tính rồi tính sai — đúng cái
`grounding.py:129-131` đang cấm ở phía trả lời, chỉ khác là ở đây chưa ai cấm.

File có ghi sẵn số ngày mà lệch với hiệu hai mốc thì **báo cho admin**, đừng âm
thầm chọn một bên: lệch đó thường là dấu hiệu file có lỗi, không phải dấu hiệu
cần sửa dữ liệu.

### 6. `extract` hỏng không huỷ lượt nạp

`extract` là node duy nhất trong nhánh nạp KHÔNG có cạnh về `report`. Trích được
0 dòng thì tài liệu vẫn có chunk, vẫn tra được bằng `search_docs` — huỷ cả lượt
là mất luôn phần đang chạy tốt để trừng phạt phần mới.

Đánh đổi: admin có thể không nhận ra bảng trống nếu không đọc kỹ tin báo. Vì
vậy `report` phải in số dòng ở MỌI lượt, kể cả khi bằng 0.

### 7. Bỏ `list_tables` / `get_schema`

Vòng discovery của pattern SQL agent sinh ra cho trường hợp **không biết trước
schema** — cắm vào DB lạ rồi tự dò. Của ta ngược lại: một bảng, mười sáu cột,
biết từ lúc viết code. Đó là **hằng số**, dán thẳng vào system prompt.

Và vì `tar_ro` chỉ được `GRANT` trên đúng một bảng, `list_tables` vốn dĩ luôn
trả về đúng bảng đó. Không nguy hiểm — chỉ vô ích, đổi lấy hai lượt LLM và ~2
giây mỗi câu hỏi, cộng vào một lượt vốn đã có `identify_project` + `agent` +
`compose`.

Vòng đáng giữ là **repair**, và nó ánh xạ một-một với cặp `grade → rewrite`.

### 8. `validate` bằng Python thay `query_checker` bằng LLM

Bốn luật (một câu, mở đầu SELECT/WITH, không `;` giữa câu, không tên đủ điều
kiện) bắt được gần hết thứ `query_checker` bắt. Khác biệt: chạy tức thì, không
tốn lượt LLM, và **không bao giờ nghĩ ra hai kết luận khác nhau cho cùng một
câu** — thứ mà một model chấm SQL luôn có thể làm.

Đánh đổi: không bắt được SQL sai về mặt ngữ nghĩa (đúng cú pháp, sai ý). Nhưng
cái đó `query_checker` cũng không bắt được đáng tin, còn `execute` thì bắt được
mọi lỗi cú pháp thật và đưa nguyên văn cho `repair`.

### 9. `coverage: partial` — sửa tool thay vì phình prompt

Câu hỏi "những việc nào đang thực hiện?" có 10 kết quả, `k = 5`. Vấn đề KHÔNG
phải model chọn nhầm tool — mà là `search_docs` trả 5 đoạn **không hề báo rằng
đó chỉ là 5 trong nhiều**. Model không có cách nào biết mình đang nhìn một phần.

Và `grounding.check` không cứu được ca này: model đếm ra "4 việc" từ 5 đoạn, số
4 có thật trong tài liệu nên lưới cho qua. Đúng ranh giới `grounding.py` tự ghi
— chặn bịa SỐ, không chặn bịa Ý.

Thêm một khoá vào payload là sự thật LÚC CHẠY, model tự đối chiếu. Một câu trong
docstring ("luôn trả phần liên quan nhất, không phải toàn bộ") phủ hết mọi biến
thể — "liệt kê tất cả", "còn những gì", "nhóm nào nhiều nhất" — vì nó nói về
**giới hạn của công cụ**, thứ bất biến, chứ không về chủ đề câu hỏi, thứ vô hạn.

Ba tầng, và case KHÔNG thuộc tầng một:

| Tầng | Chứa gì | Phình theo số case? |
|---|---|---|
| Docstring / system prompt | Ranh giới bất biến | Không |
| Payload của tool | `coverage`, `status` | Không |
| Vòng `MISSING` | Sửa sai của đúng lượt đó | Không |

### 10. Không thêm node router chọn tool

Cám dỗ là dựng một node phân loại "câu này SQL hay RAG" trước `agent`. Đó là
lượt LLM thứ ba mỗi lượt hỏi, và nó sẽ mâu thuẫn với chính phán đoán của `agent`
— router bảo SQL, agent vẫn gọi RAG, giờ tin ai?

Chọn tool là việc tool-calling sinh ra để làm. Cần đúng hai thứ: docstring tốt,
và `max_tool_rounds` đủ rộng để gọi được cả hai.

### 11. Admin graph mất tính chất "không gọi LLM"

Đây là mất mát thật, ghi ra để không ai tưởng nó vẫn còn. Trước: chi phí một
lượt nạp là vài lượt ghi checkpoint + một lượt embedding. Sau: cộng thêm 1 lượt
LLM mỗi sheet (`.xlsx`) hoặc N/8 lượt (`.txt`).

Kéo theo ba thứ mới phải có: gom lô, `Semaphore` giới hạn song song, `tenacity`
retry — rate limit Gemini sẽ chạm khi nạp file lớn. Và admin phải chờ lâu hơn.

### 12. `ghi_chu` tự do thay `trang_thai` có enum

Bản đầu đặt một cột `trang_thai` với `CHECK` bốn giá trị. File thật không có
cột đó. Cái nó có là **Ghi chú**, và nội dung là câu chữ người viết:
"Dự kiến xin cấp GPMT do yếu tố đặc thù", "Bước thẩm tra tính hiệu quả, tính
khả thi của dự án có thể là Hội đồng thẩm định của người QĐ đầu tư thành lập".

Ép những câu đó vào bốn giá trị là bắt LLM **quyết định thay người viết**, ở
tầng nạp — tầng không có ai đọc lại. Hỏng theo hai hướng cùng lúc: hoặc nó ánh
xạ bừa và ta có một cột trạng thái trông sạch nhưng bịa; hoặc `rowcheck` loại
dòng, và ta mất luôn ngày tháng đúng của dòng đó chỉ vì một ô chú thích.

Chép nguyên văn thì tệ nhất là câu trả lời phải trích lại chú thích thay vì lọc
theo nó — chậm hơn, nhưng đúng. Tiến độ vốn suy được từ `ngay_ht` với hôm nay;
`ghi_chu` là thứ giải thích *vì sao*, và giải thích thì phải giữ nguyên chữ.

Hệ quả: `rowcheck` còn ba luật, `CHECK` constraint biến mất, và câu hỏi enum
trong "Chưa chốt" đóng lại — không có enum nào để chốt.

### 13. Bỏ `du_an` — nó là `project.name`, không phải dữ liệu cần trích

Cám dỗ ban đầu là để LLM đọc tên dự án từ nội dung file. Sai ở gốc: dự án
KHÔNG được xác định bằng nội dung file — nó được xác định TRƯỚC khi đọc file,
ở node `ask_project`. Nghĩa là `project_id` (và do đó `project.name`) đã có
sẵn, chắc chắn, trước khi `extract` chạy dòng đầu tiên. Cho LLM đọc lại một
thứ đã biết là tạo ra một bản sao có thể lệch chữ so với bản gốc — đúng bug
`schema.sql:18-20` ("Đội xe" / "đội xe ").

Hệ quả kéo theo: muốn hiển thị tên dự án lúc trả lời câu hỏi SQL thì KHÔNG
`JOIN public.project` trong câu SQL của LLM — `tar_ro` bị `REVOKE ALL ON
SCHEMA public`, JOIN đó sẽ nổ permission denied. Tên dự án phải lấy ở tầng
app, từ state hội thoại đã có sẵn (`identify_project` đã resolve nó trước đó
trong cùng lượt hỏi).

### 14. `can_cu_phap_ly` / `ket_qua_dau_ra` vào bảng, cùng khuôn với `ghi_chu`

Ảnh mẫu thật có 8 cột, không phải 7 như bản nháp đầu giả định. Bỏ sót hai cột
này khỏi bảng có nghĩa là loại đúng nhóm câu hỏi mà cả kế hoạch được viết ra
để trả lời đúng: "việc nào chưa có kết quả đầu ra / biên bản nghiệm thu" là
câu lọc-đếm điển hình, và nếu `ket_qua_dau_ra` không có trong bảng thì câu đó
buộc phải qua `search_docs` — quay lại chính giới hạn `k = 5` ban đầu.

Chi phí gần như bằng không: cùng một lượt LLM/cùng một dòng Excel đang đọc,
thêm hai trường TEXT vào schema không tốn thêm lượt gọi nào. Xử lý giống hệt
`ghi_chu` — chép nguyên văn, không phải lý do loại dòng ở `rowcheck`.

---

# PHẦN 3 — CẠM BẪY PHÁT HIỆN KHI ĐỌC REPO

### `grounding._tool_text` chỉ đọc `passages` — chặn cứng

`graph_client/helpers/grounding.py:96`:

```python
for passage in payload.get("passages", []) if isinstance(payload, dict) else []:
```

`query_data` trả `{"rows": [...]}` → vòng lặp này chạy 0 lần → `source` không có
số nào → mọi số trong câu trả lời vào `invented` → `check` trả lý do → `compose`
ghi đè verdict thành "thiếu" → đá về `agent` → chạm trần → `respond` dán `HEDGE`.

Bot trả lời đúng và tự phủ nhận mình. **Sửa trước khi thử tool lần đầu**, không
thì nửa buổi debug đi tìm nhầm chỗ.

### `compose.md` cấm tự tính — mâu thuẫn với `SUM()`

Câu dặn hiện tại đúng cho RAG (chép số nguyên văn từ passage) nhưng sai cho SQL:
`SUM()`, `COUNT()`, `AVG()` sinh ra số **không có trong tài liệu nào**. Nó hợp
lệ vì Postgres tính. Phải phân biệt rõ trong prompt, không thì `compose` tự thấy
có lỗi ở mọi câu trả lời có tổng hợp.

### `max_tool_rounds = 3` chật với hai tool

Một câu hỏi cần cả hai tool là đã 2 vòng. Còn 1 vòng cho mọi việc khác. Nâng 5.

### Hash chặn backfill

`check_file` trả `unchanged` khi `sha256` trùng, và đó là nhánh chặn TRƯỚC
`parse` — nên tài liệu nạp trước khi có `extract` sẽ không bao giờ chạy qua nó.

Cột `source_document.documents` chỉ giữ đầu ra loader (text), không giữ cell, nên
cũng không tái extract từ DB được cho `.xlsx`. Cần một lệnh `/reindex <dự án>`
bỏ qua kiểm hash, hoặc chấp nhận admin nạp lại tay.

### Docstring `graph_admin/graph.py` sẽ sai

Dòng "Nhánh nạp KHÔNG gọi LLM. Chi phí của graph này là vài lượt ghi checkpoint"
là mô tả đúng của hiện tại và sai hẳn sau Bước 6. Sửa cùng lúc với việc thêm
node — chú thích sai nguy hiểm hơn không có chú thích.

### `models.yaml` khối `admin` vẫn chưa ai đọc

Chú thích ở `models.yaml:20-28` ghi rõ khối này chưa dùng vì `ADMIN_TOOLS` rỗng.
Vẫn đúng sau kế hoạch này — node `extract` dùng khối `models.extract` riêng,
không dùng `admin`. Đừng tiện tay gán vào đó.

### RLS: chủ bảng bypass policy — và app NỐI BẰNG role chủ

Đây là cạm bẫy nguy hiểm nhất trong cả kế hoạch, vì nó hỏng theo hướng MỞ và
im lặng. `DATABASE_URL` là role chủ của `data.cong_viec`, mà chủ bảng bỏ qua
policy RLS **không báo gì**. Nghĩa là: chạy SQL của LLM bằng một session thường
thì mọi guardrail ở Bước 0 trở thành trang trí — model thấy dòng của mọi dự án,
và câu trả lời trông vẫn rất thật.

Thứ bật RLS lên không phải `CREATE POLICY`, mà là `SET LOCAL ROLE tar_ro`.
Không có exception nào khi thiếu nó. Vì vậy:

- `readonly_tx` là chỗ DUY NHẤT được mở session cho SQL của LLM. `execute`
  không tự `Session(engine)` trong bất kỳ hoàn cảnh nào.
- Test đầu tiên viết cho subgraph SQL phải là: nạp hai dự án, hỏi dự án A,
  khẳng định không có dòng nào của B trong `rows`. Đó là test bắt được lỗi này;
  không test nào khác bắt được.
- `tar_ro` cũng không được là owner bảng, cùng lý do.

### `SET LOCAL` cần transaction

Ngoài transaction, `SET LOCAL` không có tác dụng và `current_setting` trả rỗng →
policy loại hết → 0 dòng. `readonly_tx` mở `session.begin()` ngay, đừng dùng
autocommit.

Và `SET LOCAL ROLE` cũng cần transaction để mà `LOCAL` — không có nó thì role
đổi ở phạm vi SESSION, connection ấy quay lại pool với quyền `tar_ro`, rồi một
lượt NẠP nào đó sau này ngã vì "permission denied for table doc_chunk". Lỗi sẽ
hiện ở một chỗ hoàn toàn không liên quan, sau một khoảng ngẫu nhiên.

### `ALTER ROLE ... SET` không áp cho role NOLOGIN

`search_path`, `statement_timeout`, `default_transaction_read_only` đặt bằng
`ALTER ROLE tar_ro SET ...` chỉ có hiệu lực lúc **đăng nhập**, mà `tar_ro`
không bao giờ đăng nhập. Ba thứ đó phải đặt bằng `SET LOCAL` trong
`readonly_tx`. Thiếu `search_path` là `FROM cong_viec` nổ "relation does not
exist" — và `validate` đang cấm model viết `data.` nên model không tự chữa
được, nó chỉ quay vòng `repair` cho đến hết `max_sql_attempts`.

### `route` phải dọn ba khoá mới

`graph_admin/nodes/route.py` là chỗ DUY NHẤT dọn state, và các khoá ngoài
`messages` không có reducer. Thêm `rows` / `rows_rejected` / `extract_error` mà
quên dọn thì một lượt nạp hỏng để lại `extract_error` cho mọi lượt sau — đúng
cái bug docstring của `route` đang cảnh báo, chỉ với khoá mới.

### Neon và `CREATE ROLE`

Neon hạn chế tạo role **đăng nhập được** qua SQL — nhưng `tar_ro` là `NOLOGIN`,
không có mật khẩu, nên `CREATE ROLE tar_ro NOLOGIN` chạy bằng SQL bình thường.
Đây là lợi ích kèm theo của việc bỏ DSN thứ hai: cả Bước 0 chạy trong một
phiên `psql`, không phải mở console.

Nếu `GRANT tar_ro TO CURRENT_USER` nổ trên bản Postgres cũ thì thay
`CURRENT_USER` bằng tên role chủ viết thẳng (xem `SELECT current_user`).
