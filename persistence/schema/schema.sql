
CREATE EXTENSION IF NOT EXISTS vector;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS project (
    project_id      UUID PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_document (
    document_id    UUID PRIMARY KEY,
    project_id     UUID NOT NULL REFERENCES project (project_id),

    -- Tên đã bỏ hậu tố "(n)" mà Telegram/Windows thêm khi tải trùng tên.
    file_name      TEXT NOT NULL,
    file_kind      TEXT NOT NULL CHECK (file_kind IN ('txt', 'xlsx')),

    content_sha256 TEXT NOT NULL,

    as_of_date     DATE NOT NULL,
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

    project_id   UUID NOT NULL,

    as_of_date   DATE NOT NULL,

    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,         
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,

    embedding    VECTOR(768) NOT NULL,

    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunk_project ON doc_chunk (project_id);
 
CREATE INDEX IF NOT EXISTS idx_chunk_vec ON doc_chunk
    USING hnsw (embedding vector_cosine_ops);

CREATE SCHEMA IF NOT EXISTS data;
 
CREATE OR REPLACE FUNCTION data.word_similarity(text, text)
RETURNS real
LANGUAGE sql IMMUTABLE PARALLEL SAFE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT public.word_similarity($1, $2)
$$;

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
    -- trên — lưu thêm ở đây là mở lại đúng bug ở phần 1 ("Đội xe" và "đội xe "
    -- tách thành hai). Tên dự án lấy bằng JOIN public.project ở tầng app
    -- (project name đã có sẵn trong state hội thoại), KHÔNG qua SQL của LLM —
    -- tar_ro không có quyền đọc schema public.
    giai_doan   TEXT,   -- mã cấp I, VD "Chuẩn bị đầu tư"
    nhom        TEXT,   -- mã cấp I.1 / I.2
    nhom_con    TEXT,   -- mã cấp I.2.1, có thể NULL nếu file không phân đến cấp này
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
