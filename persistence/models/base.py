"""Thứ dùng chung của các model. Không chứa bảng nào."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Mốc mặc định cho các cột thời gian.

    LUÔN có tzinfo (UTC), không dùng datetime.utcnow() vốn trả giờ UTC mà lại
    naive — so một mốc naive với một mốc aware là ném TypeError giữa lúc chạy.
    """
    return datetime.now(timezone.utc)
