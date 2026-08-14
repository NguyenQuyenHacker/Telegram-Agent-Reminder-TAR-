"""Tầng logic của luồng nạp. Hàm thuần — không biết state, không biết graph.

    loaders.py   đường dẫn file -> list[Document]
    split.py     list[Document] -> chunk nhỏ hơn
    filename.py  tên file -> danh tính tài liệu + mốc dữ liệu
    writer.py    chunk + vector -> ghi 3 bảng trong một transaction

Không dính state nên pytest gọi thẳng được, khỏi dựng graph.
"""
