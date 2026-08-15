"""Lớp kiểm dòng — hàm thuần, không LLM, không DB.

Đây là tuyến phòng thủ cuối trước khi số liệu vào bảng. Test ở đây rẻ và nhanh
nên nó phải phủ kín, khác hẳn phần gọi model bên trên.
"""

from datetime import date

import pytest

from TAR_agent.graph_admin.helpers.rowcheck import check
from TAR_agent.graph_admin.helpers.rows import CongViecRow, RawRow


def raw(**kwargs) -> RawRow:
    so_ngay = kwargs.pop("so_ngay_in_file", None)
    kwargs.setdefault("cong_viec", "Khảo sát hiện trạng")
    return RawRow(row=CongViecRow(**kwargs), so_ngay_in_file=so_ngay)


SOURCE = "Khảo sát hiện trạng | Ban QLDA | 2026-06-01 | 2026-06-05 | 5"


class TestLuat1TenCongViec:
    def test_ten_rong_thi_bo(self):
        kept, notes = check([raw(cong_viec="   ")], SOURCE)
        assert kept == []
        assert "không có tên công việc" in notes[0]

    def test_ten_duoc_trim(self):
        kept, _ = check([raw(cong_viec="  Khảo sát hiện trạng  ")], SOURCE)
        assert kept[0].cong_viec == "Khảo sát hiện trạng"


class TestLuat2NgayPhaiCoThat:
    """Ngày là thứ LLM bịa êm nhất — nó luôn trông hợp lý."""

    def test_ngay_co_trong_tai_lieu_thi_giu(self):
        kept, _ = check([raw(ngay_bd=date(2026, 6, 1), ngay_ht=date(2026, 6, 5))], SOURCE)
        assert len(kept) == 1

    def test_ngay_KHONG_co_trong_tai_lieu_thi_bo_CA_DONG(self):
        kept, notes = check([raw(ngay_bd=date(2026, 9, 30))], SOURCE)
        assert kept == []
        assert "2026-09-30" in notes[0]

    def test_khong_co_ngay_nao_thi_van_giu(self):
        """Nhiều dòng thật chỉ có tên việc và đơn vị. Không có ngày để bịa."""
        kept, _ = check([raw(don_vi="Ban QLDA")], SOURCE)
        assert len(kept) == 1

    @pytest.mark.parametrize(
        "viet_trong_file",
        ["30/9/2026", "30-9-2026", "2026-09-30", "30/09/26"],
        ids=["gach-cheo", "gach-ngang", "iso", "nam-hai-so"],
    )
    def test_moi_kieu_gõ_ngay_deu_doi_chieu_duoc(self, viet_trong_file: str):
        """Dùng CHUNG regex với grounding.py — hai đầu hệ thống phải hiểu "một
        cái ngày" giống hệt nhau."""
        kept, _ = check(
            [raw(ngay_ht=date(2026, 9, 30))], f"Thi công xong {viet_trong_file}"
        )
        assert len(kept) == 1

    def test_ngay_khong_ghi_nam_van_khop(self):
        """File thật hay viết "30/9". Chặt quá thì loại đúng dòng có thật."""
        kept, _ = check([raw(ngay_ht=date(2026, 9, 30))], "hoàn thành 30/9")
        assert len(kept) == 1


class TestLuat3ThuTuHaiMoc:
    def test_ngay_ht_truoc_ngay_bd_thi_bo(self):
        source = "01/06/2026 và 05/06/2026"
        kept, notes = check(
            [raw(ngay_bd=date(2026, 6, 5), ngay_ht=date(2026, 6, 1))], source
        )
        assert kept == []
        assert "trước ngày bắt đầu" in notes[0]

    def test_hai_moc_bang_nhau_thi_giu(self):
        kept, _ = check(
            [raw(ngay_bd=date(2026, 6, 1), ngay_ht=date(2026, 6, 1))], SOURCE
        )
        assert kept[0].so_ngay == 1


class TestSoNgay:
    """TÍNH bằng Python, không nhận từ LLM."""

    def test_tinh_tu_hai_moc_va_TINH_CA_NGAY_DAU(self):
        kept, _ = check([raw(ngay_bd=date(2026, 6, 1), ngay_ht=date(2026, 6, 5))], SOURCE)
        assert kept[0].so_ngay == 5  # không phải 4

    def test_thieu_mot_moc_thi_khong_tinh(self):
        kept, _ = check([raw(ngay_bd=date(2026, 6, 1))], SOURCE)
        assert kept[0].so_ngay is None

    def test_thieu_moc_nhung_file_ghi_san_thi_NHAN_so_cua_file(self):
        """Con số đó do người viết file điền, không phải LLM sinh ra."""
        kept, _ = check([raw(so_ngay_in_file=7)], SOURCE)
        assert kept[0].so_ngay == 7

    def test_file_ghi_lech_thi_BAO_ADMIN_nhung_VAN_GIU_dong(self):
        """Lệch thường là dấu hiệu file có lỗi, không phải dấu hiệu dòng này sai."""
        kept, notes = check(
            [raw(ngay_bd=date(2026, 6, 1), ngay_ht=date(2026, 6, 5), so_ngay_in_file=99)],
            SOURCE,
        )
        assert len(kept) == 1, "dòng có số liệu đúng không được vứt"
        assert kept[0].so_ngay == 5, "lấy hiệu hai mốc, không lấy số file ghi"
        assert "99" in notes[0] and "5" in notes[0], "phải nêu CẢ HAI con số"


class TestBaTruongTuDo:
    """KHÔNG có luật nào cho chúng, và đó là quyết định chứ không phải thiếu sót."""

    def test_ghi_chu_la_van_xuoi_la_van_KHONG_lam_dong_bi_loai(self):
        kept, _ = check(
            [
                raw(
                    ghi_chu="Dự kiến xin cấp GPMT do yếu tố đặc thù",
                    can_cu_phap_ly="Bước thẩm tra tính hiệu quả, tính khả thi",
                    ket_qua_dau_ra="",
                )
            ],
            SOURCE,
        )
        assert len(kept) == 1
        assert kept[0].ghi_chu == "Dự kiến xin cấp GPMT do yếu tố đặc thù"

    def test_chep_NGUYEN_VAN_khong_chuan_hoa(self):
        kept, _ = check([raw(ghi_chu="  CHẬM  ")], SOURCE)
        assert kept[0].ghi_chu == "  CHẬM  "


class TestDemDongBiLoai:
    def test_so_dong_bi_loai_la_DO_LECH_khong_phai_len(self):
        """`notes` chứa cả cảnh báo của dòng VẪN ĐƯỢC GIỮ — đếm nó là đếm sai."""
        rows = [
            raw(cong_viec="A", ngay_bd=date(2026, 6, 1), ngay_ht=date(2026, 6, 5),
                so_ngay_in_file=99),          # giữ, có cảnh báo
            raw(cong_viec=""),                # loại
            raw(cong_viec="C", ngay_bd=date(2031, 1, 1)),  # loại: ngày bịa
        ]
        kept, notes = check(rows, SOURCE)
        assert len(kept) == 1
        assert len(rows) - len(kept) == 2, "đúng hai dòng bị loại"
        assert len(notes) == 3, "ba ghi chú — nhiều hơn số dòng bị loại"


class TestChuyenTiepTruong:
    def test_moi_truong_di_qua_nguyen_ven(self):
        import uuid

        chunk = uuid.uuid4()
        row = RawRow(
            row=CongViecRow(
                giai_doan="Chuẩn bị đầu tư", nhom="Lập chủ trương", nhom_con="Thẩm định",
                cong_viec="Khảo sát hiện trạng", don_vi="Ban QLDA",
                ngay_bd=date(2026, 6, 1), ngay_ht=date(2026, 6, 5),
                can_cu_phap_ly="NĐ 15", ket_qua_dau_ra="Báo cáo", ghi_chu="ok",
            ),
            chunk_id=chunk,
        )
        kept, _ = check([row], SOURCE)
        got = kept[0]
        assert (got.giai_doan, got.nhom, got.nhom_con) == (
            "Chuẩn bị đầu tư", "Lập chủ trương", "Thẩm định",
        )
        assert (got.don_vi, got.can_cu_phap_ly, got.ket_qua_dau_ra) == (
            "Ban QLDA", "NĐ 15", "Báo cáo",
        )
        assert got.chunk_id == chunk
