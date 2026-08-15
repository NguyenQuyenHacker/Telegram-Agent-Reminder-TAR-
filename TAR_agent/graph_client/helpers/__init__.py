"""Tầng logic của luồng hỏi đáp. Hàm thuần, không biết state là gì.

    retriever.py   project_id + câu hỏi -> đoạn liên quan + nguồn (BM25 + vector)
    grounding.py   câu trả lời + đoạn tra được -> lý do không bám nguồn, hay None

KHÔNG có hàm ghi nào ở đây, và sẽ không bao giờ có.
"""
