"""Rút bảng lịch công việc có cấu trúc ra khỏi file đã parse.

Node DUY NHẤT của nhánh nạp gọi LLM, và là node duy nhất KHÔNG có cạnh về
`report`: trích hỏng thì đi tiếp sang `store` với 0 dòng. Tài liệu vẫn có chunk,
vẫn tra được bằng `search_docs` — huỷ cả lượt nạp là mất luôn phần đang chạy tốt
để trừng phạt phần mới.

Hai nhánh, hai chi phí rất khác nhau:

    .xlsx   1 lượt LLM MỖI SHEET, chỉ để ánh xạ tên cột. Dữ liệu đọc bằng
            Python theo ánh xạ đó — một file 500 dòng tốn 1 lượt chứ không phải
            500, và số liệu KHÔNG đi qua model nên không có đường nào để sai.
    .txt    N/batch_chunks lượt LLM, chạy song song dưới Semaphore. Không có
            cột TT để dựa vào nên model phải tự suy phân cấp từ câu chữ.
"""

import asyncio
import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dateutil.parser import ParserError
from dateutil.parser import parse as parse_date
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage

from TAR_agent.graph_admin.helpers import loaders, rowcheck
from TAR_agent.graph_admin.helpers.rows import (
    CheckedRow,
    CongViecRow,
    HeaderMap,
    RawRow,
    RowBatch,
)
from TAR_agent.graph_admin.state import AdminState
from TAR_agent.utils.config import create_google_genai, load_config, load_prompt
from TAR_agent.utils.text import chunk_uuid, document_uuid, normalize_name

log = logging.getLogger(__name__)

# Mã mục phân cấp: "I", "I.1", "I.2.1", "1", "1.2". Số La Mã hay số Ả Rập đều
# gặp trong file thật, và cả hai đều là KÝ HIỆU CẤU TRÚC chứ không phải dữ liệu.
_LEVEL_CODE = re.compile(r"^(?:[IVXLCDM]+|\d+)(?:\.\d+)*\.?$", re.IGNORECASE)

# Thứ tự khoá ngữ cảnh theo độ sâu của mã: 1 cấp -> giai_doan, 2 -> nhom, ...
_LEVELS = ("giai_doan", "nhom", "nhom_con")

# Số dòng dữ liệu đưa kèm vào prompt ánh xạ header. Đủ để model thấy cột nào
# chứa ngày, cột nào chứa chữ; nhiều hơn chỉ làm prompt dài mà không thêm tin.
_SAMPLE_ROWS = 5


def _text(value: Any) -> str | None:
    """Ô -> chuỗi đã trim, hoặc None nếu rỗng. Không bao giờ trả chuỗi rỗng:
    `NULL` và `''` phân biệt được ở SQL, và `ket_qua_dau_ra IS NULL` là một
    trong những câu hỏi tool này sinh ra để trả lời."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_date(value: Any) -> date | None:
    """Ô -> `date`, hoặc None. KHÔNG đoán bừa.

    Excel lưu mọi ngày thành `datetime` nên phần lớn ô đi thẳng qua nhánh đầu.
    Ô chữ mới cần `dateutil`, và với `dayfirst=True` vì "3/6" trong file Việt
    là mùng 3 tháng 6 chứ không phải mùng 6 tháng 3.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        # `fuzzy=False`: "hoàn thành trong quý II" phải trả None chứ không được
        # thành một ngày nào đó gần đúng.
        return parse_date(text, dayfirst=True, fuzzy=False).date()
    except (ParserError, ValueError, OverflowError, TypeError):
        return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = _text(value)
    if not text:
        return None
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        return None


def _column_index(header: list[str], wanted: str | None) -> int | None:
    """Tên cột model trả về -> chỉ số cột. Khớp nguyên văn trước, chuẩn hoá sau.

    Model chép tên cột "gần đúng" là chuyện thường (thừa dấu cách, khác hoa
    thường, mất dấu). `normalize_name` đã có sẵn cho đúng việc này ở tầng dự án
    nên dùng lại, thay vì bịa ra một luật so khớp thứ hai.
    """
    if not wanted:
        return None
    if wanted in header:
        return header.index(wanted)
    target = normalize_name(wanted)
    if not target:
        return None
    for index, name in enumerate(header):
        if normalize_name(name) == target:
            return index
    return None


def map_columns(header: list[str], mapping: HeaderMap) -> dict[str, int]:
    """`HeaderMap` -> `{tên trường: chỉ số cột}`. Trường không dò ra thì vắng mặt."""
    resolved: dict[str, int] = {}
    for field in HeaderMap.model_fields:
        index = _column_index(header, getattr(mapping, field))
        if index is not None:
            resolved[field] = index
    return resolved


def split_rows(rows: list[list[Any]], columns: dict[str, int]) -> list[dict[str, Any]]:
    """Đi tuần tự qua các dòng, phân loại bằng cột TT. THUẦN PYTHON, không LLM —
    mã TT là ký hiệu cấu trúc, không cần suy luận ngôn ngữ.

    TT dạng mã cấp ("I", "I.1", "I.2.1") và dòng KHÔNG có ngày -> dòng TIÊU ĐỀ.
    Cập nhật ngữ cảnh theo CẤP của mã (1 cấp -> giai_doan, 2 -> nhom, 3 ->
    nhom_con), reset các cấp con phía dưới về None, và KHÔNG sinh dòng công việc.

    Mọi dòng còn lại có tên việc -> dòng CÔNG VIỆC thật, mang theo ngữ cảnh gần
    nhất phía trên cộng dữ liệu riêng của chính nó.

    Đây là lý do luật 1 của `rowcheck` hiếm khi kích hoạt với `.xlsx`: dòng tiêu
    đề đã bị lọc Ở ĐÂY, trước khi tới rowcheck.

    Không có cột `cong_viec` trong ánh xạ thì trả rỗng: thiếu đúng cột đó thì
    mọi dòng đều trượt `NOT NULL`, đọc tiếp chỉ để vứt đi.
    """
    name_at = columns.get("cong_viec")
    if name_at is None:
        return []
    tt_at = columns.get("tt")

    context: dict[str, str | None] = {level: None for level in _LEVELS}
    result: list[dict[str, Any]] = []

    def cell(row: list[Any], field: str) -> Any:
        index = columns.get(field)
        return row[index] if index is not None and index < len(row) else None

    for row in rows:
        name = _text(row[name_at]) if name_at < len(row) else None
        code = _text(cell(row, "tt")) if tt_at is not None else None
        start, finish = _to_date(cell(row, "ngay_bd")), _to_date(cell(row, "ngay_ht"))

        if code and _LEVEL_CODE.match(code) and not start and not finish:
            depth = min(len(code.rstrip(".").split(".")), len(_LEVELS))
            context[_LEVELS[depth - 1]] = name
            for deeper in _LEVELS[depth:]:
                context[deeper] = None
            continue

        if not name:
            continue

        result.append(
            {
                **context,
                "cong_viec": name,
                "don_vi": _text(cell(row, "don_vi")),
                "ngay_bd": start,
                "ngay_ht": finish,
                "so_ngay": _to_int(cell(row, "so_ngay")),
                "can_cu_phap_ly": _text(cell(row, "can_cu_phap_ly")),
                "ket_qua_dau_ra": _text(cell(row, "ket_qua_dau_ra")),
                "ghi_chu": _text(cell(row, "ghi_chu")),
            }
        )
    return result


class _ChunkIndex:
    """Tra ngược từ nội dung một dòng về chunk đã sinh ra nó.

    Khớp bằng phép "chunk nào CHỨA tên công việc": thô, nhưng đúng chiều — sai
    thì trả None chứ không gán nhầm. `chunk_id` là thứ dùng để truy ngược số
    liệu về nguồn, gán bừa còn tệ hơn để trống.
    """

    def __init__(self, document_id: uuid.UUID, chunks: list[Document]) -> None:
        self._pairs = [
            (chunk.page_content, chunk_uuid(document_id, index))
            for index, chunk in enumerate(chunks)
        ]

    def locate(self, needle: str | None) -> uuid.UUID | None:
        if not needle:
            return None
        for content, chunk_id in self._pairs:
            if needle in content:
                return chunk_id
        return None


class Extract:
    """Dựng bằng class vì có hai client LLM dựng sẵn + một Semaphore dùng chung.

    MỘT khối cấu hình (`models.extract`), HAI `output_schema`. Hai lượt khác
    nhau ở prompt và ở kiểu trả về — model, temperature, retry thì không có lý
    do gì để lệch, và tách làm hai khối là tạo ra hai chỗ phải sửa mỗi lần đổi
    model, chắc chắn có lần chỉ sửa một.
    """

    def __init__(self) -> None:
        config = load_config()
        extraction = config["extraction"]
        self.batch = extraction["batch_chunks"]
        self.semaphore = asyncio.Semaphore(extraction["max_concurrency"])

        extract_cfg = config["models"]["extract"]
        self.header_mapper = create_google_genai(extract_cfg, output_schema=HeaderMap)
        self.row_extractor = create_google_genai(extract_cfg, output_schema=RowBatch)

    # --- nhánh .xlsx ------------------------------------------------------

    async def _map_header(self, sheet: loaders.Sheet) -> HeaderMap | None:
        """MỘT lượt LLM cho cả sheet. Hỏng thì trả None -> sheet đó bỏ qua.

        Đánh đổi đã biết: header lạ (hai tầng, ô gộp) làm ánh xạ hỏng cho CẢ
        sheet chứ không phải vài dòng. Bù bằng việc `report` in ra ánh xạ đã
        dùng để admin soát lại.
        """
        sample = "\n".join(
            " | ".join(loaders.cell_text(cell) for cell in row)
            for row in sheet.rows[:_SAMPLE_ROWS]
        )
        system = load_prompt(
            "admin_system/extract_header",
            HEADER=" | ".join(sheet.header),
            SAMPLE=sample or "(sheet không có dòng dữ liệu nào)",
        )
        async with self.semaphore:
            try:
                return await self.header_mapper.ainvoke(
                    {
                        "system": [SystemMessage(system)],
                        "messages": [HumanMessage(f"Sheet: {sheet.name}")],
                    }
                )
            except Exception:
                log.exception("Ánh xạ header hỏng ở sheet %r", sheet.name)
                return None

    async def _from_xlsx(
        self, path: Path, index: _ChunkIndex
    ) -> tuple[list[CheckedRow], list[str], int, dict[str, str]]:
        """Trả `(dòng đạt, ghi chú, số dòng thô, ánh xạ header đã dùng)`.

        `số dòng thô` đi kèm vì `ghi chú` KHÔNG đếm được: nó còn chứa cảnh báo
        lệch `so_ngay` của những dòng vẫn được giữ (xem `rowcheck.check`).

        Mở lại workbook chứ không mang lưới ô qua state: LangGraph ghi
        checkpoint sau MỖI node, nhét cả lưới ô của một file 500 dòng vào state
        là đẩy chừng ấy dữ liệu xuống bảng checkpoint ở mỗi bước còn lại.
        Đọc lại một file đã nằm sẵn trên đĩa rẻ hơn nhiều.
        """
        sheets = await asyncio.to_thread(loaders.read_workbook, path)
        mappings = await asyncio.gather(*(self._map_header(s) for s in sheets))

        kept: list[CheckedRow] = []
        notes: list[str] = []
        used: dict[str, str] = {}
        total = 0

        for sheet, mapping in zip(sheets, mappings):
            if mapping is None:
                notes.append(f"Sheet «{sheet.name}»: không ánh xạ được cột.")
                continue
            columns = map_columns(sheet.header, mapping)
            if "cong_viec" not in columns:
                # Sheet vướng mắc / sheet ký nhận: model đã trả toàn null theo
                # luật 4 của prompt. Không phải lỗi, không cần làm ồn.
                log.info("Sheet %r không phải bảng lịch công việc", sheet.name)
                continue
            used[sheet.name] = ", ".join(
                f"{field}={sheet.header[at]}" for field, at in sorted(columns.items())
            )

            source = "\n".join(
                " | ".join(loaders.cell_text(cell) for cell in row)
                for row in sheet.rows
            )
            raws = [
                RawRow(
                    row=CongViecRow(
                        **{k: v for k, v in mapped.items() if k != "so_ngay"}
                    ),
                    so_ngay_in_file=mapped["so_ngay"],
                    chunk_id=index.locate(mapped["cong_viec"]),
                )
                for mapped in split_rows(sheet.rows, columns)
            ]
            sheet_kept, sheet_notes = rowcheck.check(raws, source)
            log.info(
                "Sheet %r: %d dòng thô -> %d dòng đạt", sheet.name, len(raws), len(sheet_kept)
            )
            total += len(raws)
            kept += sheet_kept
            notes += [f"«{sheet.name}» {note}" for note in sheet_notes]

        return kept, notes, total, used

    # --- nhánh .txt -------------------------------------------------------

    async def _extract_batch(self, batch: list[Document], as_of: date) -> list[CongViecRow]:
        payload = "\n\n---\n\n".join(chunk.page_content for chunk in batch)
        system = load_prompt("admin_system/extract_rows", AS_OF=as_of.isoformat())
        async with self.semaphore:
            try:
                result: RowBatch = await self.row_extractor.ainvoke(
                    {
                        "system": [SystemMessage(system)],
                        "messages": [HumanMessage(payload)],
                    }
                )
                return result.rows
            except Exception:
                log.exception("Rút dòng hỏng ở một lô chunk")
                return []

    async def _from_txt(
        self, chunks: list[Document], as_of: date, index: _ChunkIndex
    ) -> tuple[list[CheckedRow], list[str], int]:
        batches = [
            chunks[start : start + self.batch]
            for start in range(0, len(chunks), self.batch)
        ]
        results = await asyncio.gather(
            *(self._extract_batch(batch, as_of) for batch in batches)
        )

        kept: list[CheckedRow] = []
        notes: list[str] = []
        seen: set[tuple] = set()
        total = 0

        for batch, rows in zip(batches, results):
            source = "\n".join(chunk.page_content for chunk in batch)
            raws = [
                RawRow(row=row, chunk_id=index.locate(row.cong_viec)) for row in rows
            ]
            batch_kept, batch_notes = rowcheck.check(raws, source)
            notes += batch_notes
            total += len(raws)
            # Khử trùng theo (giai_doan, nhom, cong_viec, ngay_bd): chunk có
            # chồng lấn `chunk_overlap` nên một dòng nằm ở cuối lô này thường
            # nằm lại ở đầu lô sau.
            for row in batch_kept:
                key = row.dedupe_key()
                if key in seen:
                    continue
                seen.add(key)
                kept.append(row)

        return kept, notes, total

    # --- node -------------------------------------------------------------

    async def __call__(self, state: AdminState) -> dict:
        """Trả `rows`, `rows_rejected`, `rows_rejected_count`, `extract_error`,
        `column_map`.

        KHÔNG bao giờ trả `error`: trích hỏng không phải lý do huỷ lượt nạp.
        `report` đọc `extract_error` để nói rõ với admin.
        """
        empty = {
            "rows": [],
            "rows_rejected": [],
            "rows_rejected_count": 0,
            "column_map": {},
        }
        upload = state.get("upload")
        project_id = state.get("project_id")
        chunks = state.get("chunks") or []
        if upload is None or project_id is None or not chunks:
            return {**empty, "extract_error": None}

        index = _ChunkIndex(document_uuid(project_id, state["base_name"]), chunks)

        try:
            if state.get("file_kind") == "xlsx":
                kept, notes, total, used = await self._from_xlsx(upload.path, index)
            else:
                kept, notes, total = await self._from_txt(
                    chunks, state["as_of_date"], index
                )
                used = {}
        except Exception:
            log.exception("Trích dữ liệu hỏng: %s", state["base_name"])
            return {**empty, "extract_error": "extract_failed"}

        for sheet, mapping in used.items():
            log.info("Ánh xạ cột sheet %r: %s", sheet, mapping)
        log.info(
            "Trích %s: %d/%d dòng đạt, %d ghi chú",
            state["base_name"], len(kept), total, len(notes),
        )
        return {
            "rows": kept,
            "rows_rejected": notes,
            # Đếm bằng ĐỘ LỆCH chứ không bằng len(notes): `notes` còn chứa cảnh
            # báo lệch so_ngay của những dòng VẪN ĐƯỢC GIỮ.
            "rows_rejected_count": total - len(kept),
            "extract_error": None,
            "column_map": used,
        }
