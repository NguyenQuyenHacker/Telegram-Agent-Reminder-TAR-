import hashlib
from datetime import datetime

from sqlmodel import select

from persistence.models.report import Report
from persistence.pool import get_session


def compute_source_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.strip().encode("utf-8")).hexdigest()


def insert_report(raw_text: str, group: str | None = None) -> Report:
    source_hash = compute_source_hash(raw_text)
    with get_session() as s:
        existing = s.exec(select(Report).where(Report.source_hash == source_hash)).first()
        if existing is not None:
            return existing
        report = Report(
            report_id=source_hash[:16],
            source_hash=source_hash,
            group=group,
            raw_text=raw_text,
        )
        s.add(report)
        s.commit()
        s.refresh(report)
        return report


def search_reports(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    group: str | None = None,
    limit: int = 10,
) -> list[Report]:
    with get_session() as s:
        stmt = select(Report)
        if date_from:
            stmt = stmt.where(Report.received_at >= date_from)
        if date_to:
            stmt = stmt.where(Report.received_at <= date_to)
        if group:
            stmt = stmt.where(Report.group == group)
        stmt = stmt.order_by(Report.received_at.desc()).limit(limit)
        return list(s.exec(stmt).all())
