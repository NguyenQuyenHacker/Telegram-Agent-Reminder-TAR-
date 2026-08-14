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
    """Hành vi THẬT của UnstructuredExcelLoader(mode="elements"), đã chạy kiểm.

    Khác loader openpyxl tự viết trước đây ở hai điểm lớn — xem
    TestXlsxCatNhoTheoElement bên dưới.
    """

    def test_ngay_thang_KHONG_thanh_serial_number(self, xlsx_file: Path):
        """Rủi ro lớn nhất khi đổi sang thư viện này — ĐÃ KIỂM, không xảy ra.

        Lo ngại ban đầu: đi qua HTML thì `01/06/2026` thành `45809` (serial
        number của Excel) hoặc `06/30/26` tuỳ locale. Thực tế pandas giữ đúng
        ngày. Test ở lại làm chốt chặn cho lần nâng cấp thư viện sau.
        """
        content = _gop(load(xlsx_file))
        assert "2026-06-01" in content
        assert "2026-06-15" in content
        assert "45809" not in content

    def test_so_giu_nguyen_chu_so(self, xlsx_file: Path):
        content = _gop(load(xlsx_file))
        assert "100" in content
        assert "100.0" not in content

    def test_moi_document_deu_biet_no_thuoc_sheet_nao(self, xlsx_file: Path):
        docs = load(xlsx_file)
        assert all(d.metadata.get("sheet") for d in docs)
        assert {d.metadata["sheet"] for d in docs} == {"Hạng mục", "Vướng mắc"}

    def test_giu_cau_truc_bang_trong_text_as_html(self, xlsx_file: Path):
        """page_content bị duỗi phẳng thành chuỗi cách nhau bởi dấu cách; cấu
        trúc cột chỉ còn trong metadata. Đây là thứ để tái dựng bảng sau này."""
        bang = [d for d in load(xlsx_file) if d.metadata.get("text_as_html")]
        assert bang, "Phần bảng phải giữ được HTML"
        assert "<table>" in bang[0].metadata["text_as_html"]

    def test_sheet_rong_khong_sinh_document(self, empty_xlsx_file: Path):
        assert load(empty_xlsx_file) == []

    def test_khong_khoa_file_sau_khi_doc(self, xlsx_file: Path):
        # File tạm Telegram bị xoá ở report.py ngay sau khi dùng xong — nếu
        # loader (hay pandas/openpyxl bên dưới nó) giữ handle mở thì Windows
        # không cho xoá và mỗi lượt nạp để lại rác.
        load(xlsx_file)
        xlsx_file.unlink()
        assert not xlsx_file.exists()


class TestXlsxCatNhoTheoElement:
    """Hai thay đổi lớn so với loader openpyxl cũ. ẢNH HƯỞNG CHẤT LƯỢNG TRẢ LỜI.

    `mode="elements"` cắt mỗi sheet thành nhiều mẩu theo loại (Title, Table),
    và một DÒNG TRỐNG giữa bảng làm nó tách bảng thành hai element. Mẩu thứ hai
    KHÔNG mang theo hàng tiêu đề.

    Hệ quả: một chunk chỉ có `HM3 Thi công 2026-06-20 ...` mà không có tên cột,
    nên không biết `2026-06-20` là "Ngày bắt đầu" hay "Ngày hoàn thành". Đây
    đúng là thứ loader cũ cố tình tránh bằng cách giữ hàng tiêu đề ở mọi chunk.
    """

    def test_mot_sheet_ra_NHIEU_document(self, xlsx_file: Path):
        docs = load(xlsx_file)
        cua_hang_muc = [d for d in docs if d.metadata["sheet"] == "Hạng mục"]
        assert len(cua_hang_muc) > 1

    def test_dong_trong_giua_bang_lam_TACH_bang(self, xlsx_file: Path):
        """HM3 nằm sau một dòng trống nên bị tách khỏi HM1/HM2."""
        docs = load(xlsx_file)
        co_hm1 = [d for d in docs if "HM1" in d.page_content]
        co_hm3 = [d for d in docs if "HM3" in d.page_content]
        assert co_hm1 and co_hm3
        assert co_hm1[0].page_content != co_hm3[0].page_content

    @pytest.mark.xfail(
        reason="UnstructuredExcelLoader không lặp hàng tiêu đề sang mẩu bảng "
        "thứ hai. Chunk mồ côi mất ngữ cảnh cột -> câu trả lời dễ gán nhầm "
        "con số vào cột khác. Cần quyết: chấp nhận, hay tự ghép tiêu đề lại.",
        strict=True,
    )
    def test_mau_bang_thu_hai_VAN_co_hang_tieu_de(self, xlsx_file: Path):
        docs = load(xlsx_file)
        mo_coi = next(d for d in docs if "HM3" in d.page_content)
        assert "Tên hạng mục" in mo_coi.page_content


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
