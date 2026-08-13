-- Toàn bộ schema của TAR, một file duy nhất.
--   psql "$DATABASE_URL" -f persistence/schema/schema.sql
--
-- Bảng checkpoint của LangGraph KHÔNG nằm ở đây: checkpointer.setup() tự tạo
-- lúc app khởi động.

-- Không đặt tên bảng là "group" vì đó là từ khoá SQL, phải quote ở mọi query.
CREATE TABLE IF NOT EXISTS project_group (
    -- uuid5(NS_GROUP, normalized_name): tên chuẩn hoá sinh ra id, nên hai biến
    -- thể cách viết của cùng một nhóm không thể đẻ ra hai nhóm.
    group_id        UUID PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    -- Hai nhóm trùng tiền tố thì "TB-002" trỏ vào hai việc.
    prefix          TEXT NOT NULL UNIQUE,
    next_seq        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS task (
    task_id          TEXT PRIMARY KEY,
    code             TEXT UNIQUE,
    group_id         UUID NOT NULL REFERENCES project_group (group_id),

    -- Việc con: cùng bảng, chỉ khác ở con trỏ này. Nhờ vậy báo xong / hủy / đổi
    -- hạn / tra theo mã chạy nguyên xi trên việc con, không phải viết nhánh thứ
    -- hai. NULL = việc lớn (hoặc việc đứng một mình).
    -- CHỈ MỘT TẦNG: tầng ứng dụng từ chối gắn con vào một dòng đã có cha.
    parent_task_id   TEXT REFERENCES task (task_id),
    -- Số thứ tự KẾ TIẾP sẽ cấp cho việc con: "TB-002" -> "TB-002.1", "TB-002.2".
    -- Chỉ tăng, giống next_seq của nhóm: xóa một việc con rồi thêm lại không
    -- được lấy lại số cũ, nếu không "TB-002.3" trỏ vào hai thứ.
    next_sub_seq     INTEGER NOT NULL DEFAULT 1,

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

-- CREATE TABLE IF NOT EXISTS ở trên bỏ qua bảng đã tồn tại, nên hai cột việc con
-- phải thêm riêng cho DB dựng từ bản schema cũ. Chạy lại file này bao nhiêu lần
-- cũng được: cả hai câu đều IF NOT EXISTS.
ALTER TABLE task ADD COLUMN IF NOT EXISTS parent_task_id TEXT REFERENCES task (task_id);
ALTER TABLE task ADD COLUMN IF NOT EXISTS next_sub_seq INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_task_due_scan ON task (status, next_remind_at);
CREATE INDEX IF NOT EXISTS idx_task_group ON task (group_id, due_date);
-- Đọc tiến độ = quét việc con theo cha, chạy mỗi lượt nhắc gộp.
CREATE INDEX IF NOT EXISTS idx_task_parent ON task (parent_task_id);
