from datetime import date

from app.core.reminder_digest import group_by_bucket
from app.telegram.messages import _due_line, digest_text, update_confirm_text


class TestDueLine:
    def test_qua_han(self, now, make_task):
        assert "Quá hạn 3 ngày" in _due_line(make_task("A-001", due_date=date(2026, 7, 14)), now)

    def test_han_chot_hom_nay(self, now, make_task):
        assert "Hạn chót hôm nay" in _due_line(make_task("A-002", due_date=date(2026, 7, 17)), now)

    def test_con_han(self, now, make_task):
        assert "Còn 2 ngày" in _due_line(make_task("A-003", due_date=date(2026, 7, 19)), now)

    def test_chua_co_han(self, now, make_task):
        assert _due_line(make_task("A-004"), now) == "📅 Chưa có hạn"


class TestDigestText:
    def test_du_ba_ro_va_dung_tong(self, now, make_task):
        tasks = [
            make_task("TB-001", "Viết tài liệu", due_date=date(2026, 7, 10)),
            make_task("TB-002", "Quay demo", due_date=date(2026, 7, 18)),
            make_task("TB-003", "Dọn kho", due_date=date(2026, 12, 1)),
        ]
        text = digest_text(group_by_bucket(tasks, now), now)

        assert "Bạn có 3 việc cần làm" in text
        assert "QUÁ HẠN (1)" in text
        assert "ƯU TIÊN (1)" in text
        assert "BÌNH THƯỜNG (1)" in text
        for code in ("TB-001", "TB-002", "TB-003"):
            assert code in text

    def test_ro_rong_khong_hien_tieu_de(self, now, make_task):
        text = digest_text(group_by_bucket([make_task("TB-001")], now), now)
        assert "QUÁ HẠN" not in text
        assert "ƯU TIÊN" not in text

    def test_escape_ky_tu_html_trong_du_lieu_nguoi_dung(self, now, make_task):
        task = make_task("TB-009", "So sánh a < b", group="Nhóm <b>X</b>")
        text = digest_text(group_by_bucket([task], now), now)
        # Lọt một dấu '<' thô là Telegram từ chối cả tin nhắn
        assert "a &lt; b" in text
        assert "Nhóm &lt;b&gt;X&lt;/b&gt;" in text


class TestUpdateConfirmText:
    def test_ba_hanh_dong(self):
        text = update_confirm_text(
            [
                {"code": "TB-002", "content": "Viết tài liệu", "action": "done"},
                {"code": "TB-005", "content": "Quay demo", "action": "cancel"},
                {
                    "code": "QT-007",
                    "content": "Trình sếp duyệt",
                    "action": "reschedule",
                    "new_due_date": "2026-07-19",
                },
            ]
        )
        assert "TB-002 Viết tài liệu → ✅ đã xong" in text
        assert "TB-005 Quay demo → 🗑️ hủy" in text
        # Ngày phải hiện ra dạng người đọc được kèm thứ, để soát trước khi gật
        assert "19/07/2026 (Chủ Nhật)" in text
        assert '"ok"' in text
