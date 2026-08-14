-- Gỡ schema của bản nhắc việc cũ (TAR v1).
--
-- ĐỂ RIÊNG, không gộp vào schema.sql: một file vừa dựng vừa xoá là thứ người ta
-- chạy nhầm. Chỉ chạy file này nếu DB của bạn còn hai bảng đó.
--
--   psql "$DATABASE_URL" -f persistence/schema/drop_v1.sql
--   psql "$DATABASE_URL" -f persistence/schema/schema.sql

DROP TABLE IF EXISTS task;
DROP TABLE IF EXISTS project_group;
