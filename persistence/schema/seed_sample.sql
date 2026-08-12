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
INSERT INTO task (task_id, code, group_id, content, due_date, priority, status, next_remind_at)
VALUES
    ('2570fe80b62e982e', 'TB-001', 'b914ac18-ed67-5079-81b6-280b57bd12ec',
     'Bổ sung quy trình tin học hóa',
     '2026-08-19', 'normal', 'pending', '2026-08-12 08:00:00+07'),

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


-- Xem lại:
--
-- SELECT t.code, g.name, t.content, t.due_date, t.priority, t.status
-- FROM task t
-- JOIN project_group g USING (group_id)
-- ORDER BY t.due_date;
