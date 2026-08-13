-- Dữ liệu mẫu để xem schema chạy ra hình gì.
--   psql "$DATABASE_URL" -f persistence/schema/seed_sample.sql
--
-- Mọi UUID/hash dưới đây tính bằng chính các hàm trong repo, không bịa:
--   group_id = group_uuid('App Trưởng thôn, trưởng bản')   [app/core/task_code.py]
--   prefix   = derive_prefix(...) = 'TB'
--   task_id  = sha256(f'{group_id}|{normalize_content(content).lower()}')[:16]

INSERT INTO project_group (group_id, name, normalized_name, prefix, next_seq)
VALUES (
    'b914ac18-ed67-5079-81b6-280b57bd12ec',
    'App Trưởng thôn, trưởng bản',
    'app truong thon truong ban',
    'TB',
    5
)
ON CONFLICT (group_id) DO NOTHING;

-- content đã gọt "(hạn ...)": hạn chỉ sống ở cột due_date.
-- TB-001 đã được chia nhỏ, next_sub_seq = 6 vì đã cấp tới ".5".
INSERT INTO task (task_id, code, group_id, content, due_date, priority, status, next_remind_at, next_sub_seq)
VALUES
    ('2570fe80b62e982e', 'TB-001', 'b914ac18-ed67-5079-81b6-280b57bd12ec',
     'Bổ sung quy trình tin học hóa',
     '2026-08-19', 'normal', 'pending', '2026-08-12 08:00:00+07', 6)
ON CONFLICT (task_id) DO NOTHING;

INSERT INTO task (task_id, code, group_id, content, due_date, priority, status, next_remind_at)
VALUES

    ('fdf02d77469403e0', 'TB-002', 'b914ac18-ed67-5079-81b6-280b57bd12ec',
     'Gửi BCKTKT cho TĐG',
     '2026-08-19', 'normal', 'pending', '2026-08-12 08:00:00+07'),

    ('6285ff2e603cfd37', 'TB-003', 'b914ac18-ed67-5079-81b6-280b57bd12ec',
     'Gửi BCKTKT cho KH và bên TĐG',
     '2026-08-20', 'normal', 'pending', '2026-08-12 08:00:00+07'),

    ('d1a2dd99c18b1297', 'TB-004', 'b914ac18-ed67-5079-81b6-280b57bd12ec',
     'Hoàn thiện BCKTKT các nội dung có thể điền',
     '2026-08-17', 'normal', 'pending', '2026-08-12 08:00:00+07')
ON CONFLICT (task_id) DO NOTHING;

-- Việc con của TB-001. task_id của việc con là uuid ngẫu nhiên chứ không phải
-- băm nội dung (xem persistence/proc/subtasks.py) nên ở đây đặt tay cho dễ đọc.
--
-- Số thứ tự nhảy cóc ở ".2" là CỐ Ý: đầu mục đó đã bị người dùng xóa, tức là
-- hủy. Mã đã cấp thì không cấp lại, nếu không "TB-001.2" trỏ vào hai thứ.
-- Việc con đã hủy không nằm trong danh sách hiển thị lẫn mẫu số của tiến độ,
-- nên bảng chi tiết đọc ra đúng "Tiến độ: 2/4", tức một nửa.
INSERT INTO task (task_id, code, group_id, parent_task_id, content, due_date, priority, status, done_at, cancelled_at, next_remind_at)
VALUES
    ('a1b2c3d4e5f60001', 'TB-001.1', 'b914ac18-ed67-5079-81b6-280b57bd12ec', '2570fe80b62e982e',
     'Khảo sát hiện trạng',
     '2026-08-19', 'normal', 'done', '2026-08-10 10:00:00+07', NULL, '2026-08-12 08:00:00+07'),

    ('a1b2c3d4e5f60002', 'TB-001.2', 'b914ac18-ed67-5079-81b6-280b57bd12ec', '2570fe80b62e982e',
     'Đầu mục đã bỏ',
     '2026-08-19', 'normal', 'cancelled', NULL, '2026-08-10 10:05:00+07', '2026-08-12 08:00:00+07'),

    ('a1b2c3d4e5f60003', 'TB-001.3', 'b914ac18-ed67-5079-81b6-280b57bd12ec', '2570fe80b62e982e',
     'Viết tài liệu mô tả + biểu mẫu',
     '2026-08-19', 'normal', 'pending', NULL, NULL, '2026-08-12 08:00:00+07'),

    ('a1b2c3d4e5f60004', 'TB-001.4', 'b914ac18-ed67-5079-81b6-280b57bd12ec', '2570fe80b62e982e',
     'Trình sếp duyệt',
     '2026-08-19', 'normal', 'pending', NULL, NULL, '2026-08-12 08:00:00+07'),

    ('a1b2c3d4e5f60005', 'TB-001.5', 'b914ac18-ed67-5079-81b6-280b57bd12ec', '2570fe80b62e982e',
     'Họp thống nhất với phòng nghiệp vụ',
     '2026-08-19', 'normal', 'done', '2026-08-11 15:00:00+07', NULL, '2026-08-12 08:00:00+07')
ON CONFLICT (task_id) DO NOTHING;


-- Xem lại:
--
-- SELECT t.code, g.name, t.content, t.due_date, t.priority, t.status
-- FROM task t
-- JOIN project_group g USING (group_id)
-- WHERE t.parent_task_id IS NULL
-- ORDER BY t.due_date;
--
-- Tiến độ của một việc lớn:
--
-- SELECT p.code, count(*) FILTER (WHERE c.status = 'done') || '/' || count(*)
-- FROM task p JOIN task c ON c.parent_task_id = p.task_id
-- WHERE c.status <> 'cancelled'
-- GROUP BY p.code;
