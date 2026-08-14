"""Thứ cả hai graph đều cần.

    config.py    .env + tham số + factory dựng model + load_prompt
    embed.py     chỗ DUY NHẤT gọi API embedding (nạp và tra phải cùng model)
    projects.py  tên dự án -> project_id
    text.py      chuẩn hoá tiếng Việt + sinh UUID từ tên

Không import aiogram, không import app.*, không import hai graph. Chiều phụ
thuộc: app -> TAR_agent -> persistence.
"""
