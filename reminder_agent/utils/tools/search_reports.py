import asyncio
from datetime import datetime

from langchain_core.tools import tool

from persistence.proc.reports import search_reports as _search_reports


@tool
async def search_reports(
    date_from: str | None = None,
    date_to: str | None = None,
    group: str | None = None,
) -> list[dict]:
    """Tìm các báo cáo tiến độ đã nạp trước đây, trả nguyên văn.

    Args:
        date_from: từ ngày, dạng "YYYY-MM-DD".
        date_to: đến ngày, dạng "YYYY-MM-DD".
        group: lọc theo nhóm dự án nếu báo cáo có gắn nhóm.
    """
    reports = await asyncio.to_thread(
        _search_reports,
        datetime.fromisoformat(date_from) if date_from else None,
        datetime.fromisoformat(date_to) if date_to else None,
        group,
    )
    return [
        {
            "report_id": report.report_id,
            "group": report.group,
            "received_at": report.received_at.isoformat(),
            "raw_text": report.raw_text,
        }
        for report in reports
    ]
