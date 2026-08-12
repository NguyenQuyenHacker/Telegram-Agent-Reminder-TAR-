from app.core.task_code import (
    derive_prefix,
    format_code,
    parse_code,
    prefix_alternatives,
)


class TestDerivePrefix:
    def test_nhieu_tu_lay_hai_chu_cai_dau_cuoi(self):
        assert derive_prefix("App Trưởng thôn, trưởng bản") == "TB"
        assert derive_prefix("Hệ thống báo cáo") == "BC"

    def test_mot_tu_lay_hai_chu_dau(self):
        assert derive_prefix("Kho") == "KH"

    def test_tu_qua_ngan_van_du_hai_ky_tu(self):
        assert derive_prefix("A") == "AX"

    def test_bo_dau_tieng_viet(self):
        # Đ không tách được bằng NFD nên phải thay tay, dễ sót
        assert derive_prefix("Đội xe") == "DX"

    def test_ten_khong_co_chu_cai_nao(self):
        assert derive_prefix("!!!") == "XX"

    def test_khong_phan_biet_hoa_thuong(self):
        assert derive_prefix("hệ thống báo cáo") == derive_prefix("HỆ THỐNG BÁO CÁO")


class TestPrefixAlternatives:
    def test_khong_bao_gio_lap_lai_tien_to_goc(self):
        group = "Hệ thống báo cáo"
        alternatives = [next(prefix_alternatives(group)) for _ in range(1)]
        assert derive_prefix(group) not in alternatives

    def test_toan_chu_cai_khong_co_so(self):
        # "TB2-001" mà người dùng gõ tắt "tb2" thì lẫn với "TB-002"
        candidates = [c for _, c in zip(range(10), prefix_alternatives("Kho hàng"))]
        assert all(c.isalpha() for c in candidates), candidates

    def test_uu_tien_dai_them_mot_chu_cai_dau(self):
        assert next(prefix_alternatives("App Trưởng thôn, trưởng bản")) == "TTB"


class TestFormatCode:
    def test_dem_ba_chu_so(self):
        assert format_code("TB", 2) == "TB-002"
        assert format_code("TB", 123) == "TB-123"

    def test_vuot_ba_chu_so_thi_khong_cat(self):
        assert format_code("TB", 1234) == "TB-1234"


class TestParseCode:
    def test_cac_kieu_go_tat(self):
        assert parse_code("TB-002") == "TB-002"
        assert parse_code("tb2") == "TB-002"
        assert parse_code("TB-2") == "TB-002"
        assert parse_code("#TB-002") == "TB-002"
        assert parse_code("  TB002  ") == "TB-002"

    def test_tien_to_ba_chu_cai(self):
        assert parse_code("ttb1") == "TTB-001"

    def test_khong_phai_ma_thi_tra_none(self):
        assert parse_code("xong rồi") is None
        assert parse_code("viết tài liệu mô tả") is None
        assert parse_code("2 ngày nữa") is None
        assert parse_code("") is None

    def test_khong_bat_nham_mau_giua_chuoi_khac(self):
        assert parse_code("mã abc 123 def") is None

    def test_chuoi_trong_nhu_ma_van_duoc_nhan(self):
        # Không phân biệt được "abc123" với một mã thật. find_tasks_by_reference
        # tra mã không thấy ai thì tự rơi về tìm theo nội dung, nên chỗ này chỉ
        # cần nhận dạng rộng tay.
        assert parse_code("abc123") == "ABC-123"
