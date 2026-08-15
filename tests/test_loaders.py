from pathlib import Path

import pytest
from langchain_core.documents import Document

from TAR_agent.graph_admin.helpers.loaders import SUPPORTED, UnsupportedFormat, file_kind, load
from TAR_agent.graph_admin.helpers.split import split_documents


class TestFileKind:
    def test_ha_thuong_va_bo_dau_cham(self):
        assert file_kind("BaoCao.PDF") == "pdf"
        assert file_kind("Tiendo.xlsx") == "xlsx"

    def test_khop_gia_tri_CHECK_trong_schema(self):
        """Đã bỏ .pdf — CHECK trong schema.sql phải khớp đúng tập này."""
        assert {file_kind(f"x{s}") for s in SUPPORTED} == {"txt", "xlsx"}


class TestLoadTxt:
    def test_doc_duoc_tieng_viet(self, txt_file: Path):
        docs = load(txt_file)
        assert len(docs) == 1
        assert "Hạng mục 3 chậm do chờ mặt bằng." in docs[0].page_content
        assert docs[0].metadata["source"].endswith("GhiChu.txt")


def _gop(docs) -> str:
    return "\n".join(d.page_content for d in docs)


class TestLoadXlsx:
    """Hành vi của loader openpyxl. `UnstructuredExcelLoader` đã bỏ hẳn.

    Ba test dưới đây từng khẳng định điều NGƯỢC LẠI (một sheet ra nhiều
    Document, `text_as_html` trong metadata, dòng trống tách bảng) — đó là hành
    vi của `mode="elements"`, và nó chính là lý do phải đổi loader: mẩu bảng
    thứ hai không mang theo hàng tiêu đề, nên một chunk chỉ có
    `HM3 Thi công 2026-06-20` mà không biết ngày đó là ngày bắt đầu hay kết thúc.
    """

    def test_ngay_thang_KHONG_thanh_serial_number(self, xlsx_file: Path):
        """`01/06/2026` phải ra `2026-06-01`, không phải `45809` (serial Excel)."""
        content = _gop(load(xlsx_file))
        assert "2026-06-01" in content
        assert "2026-06-15" in content
        assert "45809" not in content

    def test_so_giu_nguyen_chu_so(self, xlsx_file: Path):
        """openpyxl trả float cho mọi ô số; `cell_text` phải cắt phần `.0`."""
        content = _gop(load(xlsx_file))
        assert "100" in content
        assert "100.0" not in content

    def test_moi_document_deu_biet_no_thuoc_sheet_nao(self, xlsx_file: Path):
        docs = load(xlsx_file)
        assert all(d.metadata.get("sheet") for d in docs)
        assert {d.metadata["sheet"] for d in docs} == {"Hạng mục", "Vướng mắc"}

    def test_MOT_sheet_ra_DUNG_MOT_document(self, xlsx_file: Path):
        """Cắt nhỏ là việc của splitter, không phải của loader.

        Loader trả nguyên một bảng markdown cho mỗi sheet; chỗ nào cắt và có
        chồng lấn bao nhiêu do `chunking` trong models.yaml quyết định — một
        chỗ, không phải hai.
        """
        docs = load(xlsx_file)
        assert len([d for d in docs if d.metadata["sheet"] == "Hạng mục"]) == 1

    def test_dong_trong_KHONG_lam_tach_bang(self, xlsx_file: Path):
        """HM3 nằm sau một dòng trống nhưng vẫn ở cùng bảng với HM1."""
        content = next(d for d in load(xlsx_file) if d.metadata["sheet"] == "Hạng mục")
        assert "HM1" in content.page_content
        assert "HM3" in content.page_content

    def test_moi_dong_deu_o_duoi_hang_tieu_de(self, xlsx_file: Path):
        """Đây là thứ `mode="elements"` làm hỏng, và là lý do đổi loader."""
        content = next(d for d in load(xlsx_file) if d.metadata["sheet"] == "Hạng mục")
        lines = content.page_content.splitlines()
        header_at = next(i for i, l in enumerate(lines) if "Tên hạng mục" in l)
        hm3_at = next(i for i, l in enumerate(lines) if "HM3" in l)
        assert header_at < hm3_at

    def test_KHONG_con_text_as_html_trong_metadata(self, xlsx_file: Path):
        """Nó nhân bản cả bảng vào metadata của MỌI chunk rồi xuống cột JSONB."""
        assert all("text_as_html" not in d.metadata for d in load(xlsx_file))

    def test_sheet_rong_khong_sinh_document(self, empty_xlsx_file: Path):
        assert load(empty_xlsx_file) == []

    def test_khong_khoa_file_sau_khi_doc(self, xlsx_file: Path):
        # File tạm Telegram bị xoá ở report.py ngay sau khi dùng xong — nếu
        # loader giữ handle mở thì Windows không cho xoá và mỗi lượt nạp để
        # lại rác. `read_only=True` của openpyxl giữ handle tới file zip, nên
        # đây không phải một lo ngại lý thuyết.
        load(xlsx_file)
        xlsx_file.unlink()
        assert not xlsx_file.exists()


class TestReadWorkbook:
    """Đầu ra thứ hai của cùng một lần mở workbook — thứ `extract` đọc."""

    def test_cell_giu_nguyen_kieu_python(self, xlsx_file: Path):
        from datetime import date, datetime

        from TAR_agent.graph_admin.helpers.loaders import read_workbook

        sheet = read_workbook(xlsx_file)[0]
        # Đây là toàn bộ lý do bỏ `unstructured`: ngày còn là ngày, không phải
        # một chuỗi phải parse ngược.
        assert any(
            isinstance(cell, (date, datetime)) for row in sheet.rows for cell in row
        )

    def test_bo_qua_dong_tieu_de_bao_cao_o_tren(self, xlsx_file: Path):
        """Dòng "BÁO CÁO TIẾN ĐỘ THÁNG 6" chiếm một ô — không phải header."""
        from TAR_agent.graph_admin.helpers.loaders import read_workbook

        sheet = read_workbook(xlsx_file)[0]
        assert "Tên hạng mục" in sheet.header
        assert all("BÁO CÁO" not in cell for cell in sheet.header)


class TestLoadDispatch:
    def test_duoi_la_thi_nem_UnsupportedFormat(self, tmp_path: Path):
        path = tmp_path / "bao-cao.docx"
        path.write_bytes(b"x")
        with pytest.raises(UnsupportedFormat):
            load(path)

    def test_lay_duoi_tu_ten_goc_khi_file_tam_khong_co_duoi(self, xlsx_file: Path):
        # File tạm hay mang tên ngẫu nhiên không đuôi; đuôi thật ở tên admin gửi
        no_suffix = xlsx_file.with_name("tmp123456")
        xlsx_file.rename(no_suffix)
        docs = load(no_suffix, file_name="TiendoT6.xlsx")
        assert docs[0].metadata["sheet"] == "Hạng mục"


class TestSplit:
    def test_giu_metadata_cua_document_goc(self, xlsx_file: Path):
        chunks = split_documents(load(xlsx_file))
        assert chunks
        assert all("sheet" in c.metadata for c in chunks)

    def test_loai_chunk_rong(self):
        chunks = split_documents(
            [
                Document(page_content="   \n\n  ", metadata={"a": 1}),
                Document(page_content="nội dung thật", metadata={"a": 2}),
            ]
        )
        assert [c.page_content for c in chunks] == ["nội dung thật"]

    def test_van_ban_dai_bi_cat_va_co_chong_lan(self):
        from TAR_agent.graph_admin.helpers.split import _splitter

        size = _splitter()._chunk_size
        overlap = _splitter()._chunk_overlap
        text = "\n\n".join(f"Đoạn số {i}. " + "nội dung dài. " * 20 for i in range(30))

        chunks = split_documents([Document(page_content=text, metadata={"s": "x"})])
        assert len(chunks) > 1
        assert all(len(c.page_content) <= size for c in chunks)
        # Chồng lấn: cuối chunk n phải xuất hiện lại ở đầu chunk n+1
        tail = chunks[0].page_content[-overlap // 2 :]
        assert tail in chunks[1].page_content
