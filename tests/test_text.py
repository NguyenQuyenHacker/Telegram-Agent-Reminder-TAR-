from TAR_agent.utils.text import ascii_lower, ilike_pattern, name_uuid, normalize_name


class TestNormalizeName:
    def test_bo_dau_tieng_viet(self):
        assert normalize_name("Hệ thống báo cáo") == "he thong bao cao"

    def test_chu_D_gach_ngang(self):
        # Đ không tách được bằng NFD nên phải thay tay, dễ sót
        assert ascii_lower("Đội xe") == "doi xe"
        assert normalize_name("Đội xe") == "doi xe"

    def test_bo_so_thu_tu_dau_dong(self):
        assert normalize_name("2/ App Trưởng thôn") == normalize_name("App Trưởng thôn")

    def test_bo_emoji_va_dau_cau(self):
        assert normalize_name("📱 App Trưởng thôn, trưởng bản") == (
            "app truong thon truong ban"
        )

    def test_khong_phan_biet_hoa_thuong_va_khoang_trang_thua(self):
        assert normalize_name("  HỆ THỐNG  báo cáo ") == normalize_name(
            "Hệ thống báo cáo"
        )


class TestNameUuid:
    def test_moi_bien_the_cach_viet_ra_cung_mot_id(self):
        # Đây là thứ giữ cho file xlsx và biên bản PDF rơi về đúng một dự án
        assert name_uuid("App Trưởng thôn") == name_uuid("2/ app trưởng thôn")

    def test_ten_khac_nhau_ra_id_khac_nhau(self):
        assert name_uuid("Hệ thống báo cáo") != name_uuid("Đội xe")

    def test_on_dinh_giua_cac_lan_chay(self):
        # uuid5 chứ không phải uuid4: chạy lại phải ra y hệt, nếu không mọi
        # project_id đã lưu trỏ vào hư không
        assert str(name_uuid("Đội xe")) == str(name_uuid("Đội xe"))


class TestIlikePattern:
    def test_vo_hieu_ky_tu_dai_dien_nguoi_dung_go(self):
        # Không escape thì "100%" thành mẫu khớp mọi thứ và quét cả bảng
        assert ilike_pattern("  100%_x  ") == r"%100\%\_x%"

    def test_chuoi_thuong_giu_nguyen(self):
        assert ilike_pattern("báo cáo") == "%báo cáo%"
