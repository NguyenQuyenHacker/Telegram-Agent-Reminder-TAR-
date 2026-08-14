-- Toàn bộ schema, một file duy nhất.
--   psql "$DATABASE_URL" -f persistence/schema/schema.sql
--
-- Đang giữ DB của bản nhắc việc cũ thì chạy drop_v1.sql TRƯỚC.
--
-- Bảng checkpoint của LangGraph KHÔNG nằm ở đây: checkpointer.setup() tự tạo
-- lúc app khởi động.

CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────── 1. Dự án ───────────────────────────
-- project_id = uuid5(NS, normalized_name) — xem TAR_agent/utils/text.py:name_uuid.
-- Suy được từ tên nên không cần tra DB mới biết một dự án mang id nào, và mọi
-- biến thể cách viết đều rơi về đúng một dòng.
CREATE TABLE IF NOT EXISTS project (
    project_id      UUID PRIMARY KEY,
    name            TEXT NOT NULL,
    -- UNIQUE đặt ở đây chứ không ở `name`: đặt ở `name` thì "Đội xe" và
    -- "đội xe " lọt thành hai dự án, tài liệu chia đôi, tra bên nào cũng thiếu.
    normalized_name TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────── 2. Tài liệu nguồn ───────────────────────────
-- DANH TÍNH của một tài liệu là (project_id, file_name). Nạp lại cùng tên là
-- GHI ĐÈ: xoá sạch chunk cũ, ghi chunk mới, tên giữ nguyên.
CREATE TABLE IF NOT EXISTS source_document (
    -- uuid5(NS_DOC, "{project_id}:{file_name}") — suy từ TÊN, không phải nội
    -- dung. Nhờ vậy ghi đè giữ nguyên document_id và mọi tham chiếu tới tài
    -- liệu vẫn đúng sau khi nội dung đổi.
    document_id    UUID PRIMARY KEY,
    project_id     UUID NOT NULL REFERENCES project (project_id),

    -- Tên đã bỏ hậu tố "(n)" mà Telegram/Windows thêm khi tải trùng tên.
    file_name      TEXT NOT NULL,
    file_kind      TEXT NOT NULL CHECK (file_kind IN ('txt', 'xlsx')),

    -- Băm BYTES THÔ. KHÔNG phải khoá duy nhất — chỉ để trả lời "nội dung có đổi
    -- không". Trùng hash thì bỏ qua, khỏi tốn tiền embedding lần nữa.
    content_sha256 TEXT NOT NULL,

    -- MỐC DỮ LIỆU của file, không phải ngày nạp. File tháng 6 nạp vào tháng 8
    -- vẫn phải nằm ở mốc tháng 6, nếu không so sánh theo thời gian sai hết.
    as_of_date     DATE NOT NULL,

    -- Đầu ra thô của loader: [{"page_content": ..., "metadata": {...}}, ...].
    -- Giữ cả metadata thì tái index (đổi chunk_size, đổi model embedding) không
    -- mất số trang và tên sheet, mà không phải bắt admin gửi lại file.
    documents      JSONB NOT NULL,
    chunk_count    INTEGER NOT NULL DEFAULT 0,

    uploaded_by    BIGINT NOT NULL,   -- telegram user id của admin nạp gần nhất
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Chặn trùng TRONG một dự án. Cùng một biên bản dùng chung cho hai dự án
    -- vẫn nạp được vào cả hai.
    UNIQUE (project_id, file_name)
);

CREATE INDEX IF NOT EXISTS idx_doc_project ON source_document (project_id, as_of_date DESC);

-- ─────────────────────────── 3. Chunk vector ───────────────────────────
CREATE TABLE IF NOT EXISTS doc_chunk (
    chunk_id     UUID PRIMARY KEY,
    document_id  UUID NOT NULL REFERENCES source_document (document_id) ON DELETE CASCADE,

    -- LẶP cố ý dù suy được qua document_id: mọi truy vấn retrieval BẮT BUỘC lọc
    -- theo project_id, để nó ngay đây thì bộ lọc là một WHERE thẳng chứ không
    -- phải một JOIN mà ai đó có thể quên viết.
    project_id   UUID NOT NULL,

    -- LẶP cố ý, cùng lý do — và đây là thứ chữa vấn đề "bản nào mới hơn": tìm
    -- kiếm trả về cả đoạn tháng 6 lẫn tháng 7 thì mỗi đoạn tự mang mốc của nó.
    -- BẤT BIẾN: ghi đè tài liệu là xoá và ghi lại toàn bộ chunk, không UPDATE
    -- lẻ tẻ nên không có đường nào để lệch.
    as_of_date   DATE NOT NULL,

    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,        -- page_content của Document

    -- metadata của Document. Mỗi loader điền thứ khác nhau:
    --   xlsx  {"sheet": "Hạng mục", "text_as_html": "<table>..."}
    --   txt   {"source": "ghichu.txt"}
    -- Một cột TEXT không diễn đạt được cả hai.
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- 768 chiều: gemini-embedding-001 ra 3072 nhưng được cắt xuống bằng tham số
    -- output_dimensionality (xem TAR_agent/utils/embed.py). ĐỔI SỐ NÀY là phải đổi cả
    -- EMBEDDING_DIM trong persistence/models/doc_chunk.py, VÀ re-embed toàn bộ
    -- (đọc lại từ cột `documents` ở trên).
    embedding    VECTOR(768) NOT NULL,

    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunk_project ON doc_chunk (project_id);
-- vector_cosine_ops chỉ tăng tốc cho toán tử <=>. Truy vấn dùng <-> hay <#> thì
-- Postgres bỏ index và quét toàn bảng — vẫn ra kết quả, chỉ chậm dần theo số
-- dòng cho tới lúc không dùng được nữa.
CREATE INDEX IF NOT EXISTS idx_chunk_vec ON doc_chunk
    USING hnsw (embedding vector_cosine_ops);
