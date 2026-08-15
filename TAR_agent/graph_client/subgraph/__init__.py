"""Vòng tra cứu tự sửa truy vấn, nằm TRONG tool `search_docs`.

    retrieve → grade ─ đạt ─────────────────→ trả passages
                 ├─ không đạt, attempt<2 ──→ rewrite → retrieve
                 └─ hết lượt ──────────────→ trả rỗng

Graph ngoài KHÔNG biết cụm này tồn tại, và đó là chủ ý: agent chỉ thấy một tool
trả về "có" hoặc "không có". Nếu để agent tự thử lại bằng cách gọi tool nhiều
lần với từ khoá khác, nó sẽ đốt lượt LLM đắt tiền (khối `client`) cho một việc
mà khối `rewrite` rẻ hơn mười lần làm được — và nó không có cách nào biết lần
tra vừa rồi trượt vì từ khoá sai hay vì kho thật sự không có.

Compile MỘT LẦN ở `graph.SEARCH_GRAPH`, KHÔNG checkpointer: cụm này không có
điểm dừng, không cần sống qua lượt, và mỗi lời gọi tool là một lần chạy độc lập.
"""
