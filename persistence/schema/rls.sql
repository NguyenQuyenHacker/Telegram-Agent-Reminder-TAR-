-- Role read-only + RLS cho agent text-to-SQL. CHẠY TAY, MỘT LẦN, SAU schema.sql:
--   psql "$DATABASE_URL" -f persistence/schema/rls.sql
--
-- Tách khỏi schema.sql vì `CREATE ROLE` và `CREATE POLICY` không có dạng
-- `IF NOT EXISTS`: để chung là schema.sql hết chạy lại được lần thứ hai.
--
-- Không có mật khẩu nào trong file này, và cũng không cần: role này KHÔNG đăng
-- nhập bao giờ. App vẫn nối bằng đúng `DATABASE_URL` cũ rồi hạ quyền xuống đây
-- bằng `SET LOCAL ROLE` ngay trong transaction chạy SQL của LLM (xem
-- persistence/pool.py:readonly_tx). Không có mật khẩu thứ hai để rò, để xoay
-- vòng, để quên trên máy CI.
--
-- Neon hạn chế tạo role ĐĂNG NHẬP ĐƯỢC qua SQL — `tar_ro` là NOLOGIN nên
-- `CREATE ROLE` dưới đây chạy bằng psql bình thường, không phải mở console.

CREATE ROLE tar_ro NOLOGIN;

-- Role mới THỪA HƯỞNG quyền của PUBLIC. Phải thu lại rõ ràng, không thì nó
-- vẫn USAGE được schema public.
REVOKE ALL ON SCHEMA public FROM tar_ro;
GRANT USAGE  ON SCHEMA data TO tar_ro;
GRANT SELECT ON data.cong_viec TO tar_ro;
GRANT EXECUTE ON FUNCTION data.word_similarity(text, text) TO tar_ro;

-- Cho role chủ được PHÉP hoá thân thành tar_ro. Thiếu đúng dòng này thì
-- `SET LOCAL ROLE tar_ro` nổ "permission denied to set role".
-- Bản Postgres cũ không nhận CURRENT_USER ở đây thì thay bằng tên role chủ
-- viết thẳng (`SELECT current_user` để lấy).
GRANT tar_ro TO CURRENT_USER;

-- Chốt chặn dự án. Model viết SELECT * không WHERE cũng chỉ thấy dòng của
-- đúng dự án đang hỏi. tar_ro KHÔNG phải chủ bảng nên không bypass được.
ALTER TABLE data.cong_viec ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_project ON data.cong_viec FOR SELECT TO tar_ro
    USING (project_id = current_setting('app.project_id', true)::uuid);

-- ─────────────────────── Kiểm bằng tay sau khi chạy ───────────────────────
-- Vẫn đăng nhập bằng role chủ như mọi khi — đó chính là điều đang kiểm.
--
-- BEGIN;
--   SET LOCAL ROLE tar_ro;
--   SET LOCAL search_path = data;
--   SELECT * FROM public.source_document;   -- phải NỔ: permission denied
-- ROLLBACK;
--
-- BEGIN;
--   SET LOCAL ROLE tar_ro;
--   SET LOCAL search_path = data;
--   SELECT count(*) FROM cong_viec;         -- phải ra 0 (chưa SET app.project_id)
--   SET LOCAL app.project_id = '<uuid thật>';
--   SELECT count(*) FROM cong_viec;         -- phải ra đúng số dòng của dự án đó
--   DELETE FROM cong_viec;                  -- phải NỔ: permission denied
-- ROLLBACK;
--
-- -- Kiểm cái dễ sai nhất: connection trả về pool có còn là tar_ro không.
-- SELECT current_user;                      -- phải là role CHỦ, không phải tar_ro
-- SELECT count(*) FROM data.cong_viec;      -- phải thấy MỌI dự án (chủ bypass RLS)
--
-- `SELECT current_user` sau ROLLBACK là phép kiểm quan trọng nhất: nếu nó trả
-- `tar_ro` thì `SET LOCAL` đã không LOCAL, và mọi lượt nạp sau đó dùng lại
-- connection ấy sẽ mất quyền ghi một cách ngẫu nhiên.
