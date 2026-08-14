"""Lõi agent: hai graph và phần dùng chung của chúng.

    graph_admin/   luồng nạp tài liệu — có quyền GHI vào kho
    graph_client/  luồng hỏi đáp — CHỈ ĐỌC
    utils/         cấu hình, model, embedding, prompt, phân giải dự án

Chiều phụ thuộc: app → TAR_agent → persistence. Không gì trong đây import ngược
lên `app` — lõi chạy được mà không cần Telegram, nhờ vậy test bằng pytest thuần.

`graph_admin` và `graph_client` KHÔNG import lẫn nhau, dù chung package cha.
Gộp chỗ ngồi không có nghĩa là gộp quyền.
"""
