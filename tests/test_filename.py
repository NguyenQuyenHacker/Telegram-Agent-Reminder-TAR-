from datetime import date

import pytest

from TAR_agent.graph_admin.helpers.filename import base_name, guess_as_of

TODAY = date(2026, 8, 13)


class TestBaseName:
    def test_bo_hau_to_ban_tai_trung_ten(self):
        # Telegram/Windows thêm "(1)" khi tải trùng tên. Không bỏ thì bản sửa
        # thành một tài liệu thứ hai, và bản cũ nằm lại mãi trong kho.
        assert base_name("TiendoT6(1).xlsx") == "TiendoT6.xlsx"
        assert base_name("TiendoT6 (2).xlsx") == "TiendoT6.xlsx"

    def test_giu_nguyen_ngoac_khong_phai_so(self):
        # "(ban chinh)" là tên người đặt, không phải hậu tố máy sinh
        assert base_name("Bao cao (ban chinh).pdf") == "Bao cao (ban chinh).pdf"

    def test_ha_thuong_duoi_file_va_bo_duong_dan(self):
        assert base_name("C:/tam/BaoCao.PDF") == "BaoCao.pdf"
        assert base_name("thu muc/BaoCao.PDF") == "BaoCao.pdf"

    def test_gop_khoang_trang_thua(self):
        assert base_name("  Bao   cao  T6.pdf  ") == "Bao cao T6.pdf"

    def test_file_khong_co_duoi(self):
        assert base_name("README(1)") == "README"


class TestGuessAsOf:
    def test_iso_day_du(self):
        assert guess_as_of("BaoCao_2026-06-30.pdf", TODAY) == date(2026, 6, 30)

    def test_ngay_thang_nam_kieu_viet(self):
        assert guess_as_of("BaoCao 30-06-2026.pdf", TODAY) == date(2026, 6, 30)

    def test_thang_khong_nam_lay_ngay_CUOI_thang(self):
        # Báo cáo tháng chốt cuối kỳ. Lấy 01/06 là lệch cả tháng dữ liệu.
        assert guess_as_of("TiendoT6.xlsx", TODAY) == date(2026, 6, 30)
        assert guess_as_of("tien_do_thang6.xlsx", TODAY) == date(2026, 6, 30)

    def test_thang_tuong_lai_hieu_la_nam_ngoai(self):
        # Tháng 12 nạp vào tháng 8 thì là báo cáo năm ngoái nạp muộn, không phải
        # số liệu của bốn tháng nữa
        assert guess_as_of("TiendoT12.xlsx", TODAY) == date(2025, 12, 31)

    def test_thang_2_nam_nhuan(self):
        assert guess_as_of("TiendoT2.xlsx", date(2024, 6, 1)) == date(2024, 2, 29)

    def test_thang_kem_nam(self):
        assert guess_as_of("Tiendo_T6-2025.xlsx", TODAY) == date(2025, 6, 30)

    def test_tuan_lay_chu_nhat(self):
        got = guess_as_of("BaoCao_Tuan23.xlsx", TODAY)
        assert got == date.fromisocalendar(2026, 23, 7)

    @pytest.mark.parametrize("name", ["ghichu.txt", "BaoCao.pdf", "Tiendo T99.xlsx"])
    def test_khong_doan_duoc_thi_tra_None(self, name):
        # None chứ không phải hôm nay: nơi gọi mới quyết định mặc định, và phải
        # in ra cho admin soát
        assert guess_as_of(name, TODAY) is None

    def test_iso_khong_bi_mau_thang_bat_nham(self):
        # "2026-06-30" cũng khớp mẫu tháng lỏng nếu để nó chạy trước
        assert guess_as_of("T_2026-06-30.pdf", TODAY) == date(2026, 6, 30)
