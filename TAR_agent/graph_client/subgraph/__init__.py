"""Vòng tra tài liệu tự sửa truy vấn. Nhánh thứ nhất của `tools/retrieval.py`.

    retrieve → grade ─ đạt ─────────────────→ trả passages
                 ├─ không đạt, attempt<2 ──→ rewrite → retrieve
                 └─ hết lượt ──────────────→ trả rỗng

Graph ngoài KHÔNG biết cụm này tồn tại — nó chỉ thấy một payload có `passages`
hoặc không. Việc thử lại bằng từ khoá khác nằm TRỌN ở đây, cạnh chỗ duy nhất
biết lần tra vừa rồi trượt vì từ khoá sai hay vì kho thật sự không có.

Compile MỘT LẦN ở `graph.SEARCH_GRAPH`, KHÔNG checkpointer: cụm này không có
điểm dừng và mỗi lần tra là một lần chạy độc lập.
"""
