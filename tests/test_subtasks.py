"""Phần thuần của tính năng việc con: thanh tiến độ, bảng chi tiết, gộp đề xuất.

Không đụng DB — mọi hàm ở đây nhận sẵn Task dựng trong bộ nhớ hoặc dict đã tra
xong. Ràng buộc ghi của persistence/proc/subtasks.py cần Postgres thật nên không
nằm trong bộ này.
"""

from datetime import date

from app.core.reminder_digest import group_by_bucket
from app.telegram.messages import (
    _change_label,
    digest_text,
    progress_line,
    task_detail_text,
)
from reminder_agent.graph import _merge_proposals
from reminder_agent.utils.tools.task_view import task_brief


class TestProgressLine:
    def test_dung_ti_le_va_phan_tram(self):
        assert progress_line(2, 4) == "Tiến độ: 2/4 (50%) ▰▰▰▱▱▱"

    def test_xong_het_thi_day_thanh(self):
        assert progress_line(4, 4) == "Tiến độ: 4/4 (100%) ▰▰▰▰▰▰"

    def test_chua_xong_gi_thi_thanh_rong(self):
        assert progress_line(0, 3) == "Tiến độ: 0/3 (0%) ▱▱▱▱▱▱"

    def test_khong_co_viec_con_thi_khong_chia_cho_khong(self):
        # 0/0 không được đọc thành 100%: chưa có đầu mục nào thì chưa xong gì cả
        assert progress_line(0, 0) == "Tiến độ: 0/0 (0%) ▱▱▱▱▱▱"


class TestDigestKemTienDo:
    def test_viec_da_chia_nho_thi_co_dong_tien_do(self, now, make_task):
        task = make_task("TB-001", "Bổ sung quy trình", due_date=date(2026, 7, 20))
        text = digest_text(
            group_by_bucket([task], now), now, {task.task_id: (2, 4)}
        )
        assert "Tiến độ: 2/4 (50%)" in text

    def test_viec_chua_chia_nho_thi_khong_in_gi_them(self, now, make_task):
        task = make_task("TB-001", "Bổ sung quy trình")
        assert "Tiến độ" not in digest_text(group_by_bucket([task], now), now, {})

    def test_khong_truyen_tien_do_thi_van_chay_nhu_cu(self, now, make_task):
        # Chữ ký cũ digest_text(buckets, now) phải còn dùng được
        text = digest_text(group_by_bucket([make_task("TB-001")], now), now)
        assert "TB-001" in text


class TestTaskDetailText:
    def _view(self, subtasks):
        return {
            "status": "detail",
            "task": {
                "code": "TB-001",
                "content": "Bổ sung quy trình tin học hóa",
                "due_date": "2026-07-20",
                "days_left": 3,
            },
            "subtasks": subtasks,
            "progress": {
                "done": sum(1 for s in subtasks if s["status"] == "done"),
                "total": len(subtasks),
            },
        }

    def test_dung_bieu_tuong_theo_trang_thai(self):
        text = task_detail_text(
            self._view(
                [
                    {"code": "TB-001.1", "content": "Khảo sát hiện trạng", "status": "done"},
                    {"code": "TB-001.2", "content": "Viết tài liệu", "status": "pending"},
                ]
            )
        )
        assert "✔️ TB-001.1 Khảo sát hiện trạng" in text
        assert "⬜ TB-001.2 Viết tài liệu" in text
        assert "Tiến độ: 1/2 (50%)" in text

    def test_in_han_va_so_ngay_con_lai(self):
        text = task_detail_text(self._view([]))
        assert "📌 TB-001 · Bổ sung quy trình tin học hóa" in text
        assert "Hạn 20/07" in text
        assert "Còn 3 ngày" in text

    def test_chua_chia_nho_thi_moi_nguoi_dung_chia(self):
        text = task_detail_text(self._view([]))
        assert "Chưa chia việc con nào" in text
        # Không in thanh tiến độ rỗng: nó trông như "đã chia mà chưa làm gì"
        assert "Tiến độ" not in text

    def test_han_hong_khong_lam_chet_bang(self):
        # dict đi qua checkpoint rồi mới tới đây, không tin là nó luôn sạch
        view = self._view([])
        view["task"]["due_date"] = "20/7/2026"
        assert "Chưa có hạn" in task_detail_text(view)


class TestNhanDeXuatViecCon:
    def test_them_viec_con_in_ra_noi_dung_moi(self):
        label = _change_label(
            {"action": "add_subtask", "subtask_content": "Khảo sát hiện trạng"}
        )
        # Gật vào "thêm việc con" mà không đọc được thêm cái gì thì gật mù
        assert "Khảo sát hiện trạng" in label

    def test_sua_ten_in_ra_ten_moi(self):
        label = _change_label(
            {"action": "rename_subtask", "subtask_content": "Trình sếp duyệt"}
        )
        assert "Trình sếp duyệt" in label


class TestGopDeXuatViecCon:
    def test_nhieu_viec_con_cua_cung_mot_cha_deu_song_sot(self):
        # Gộp theo task_id như ba hành động kia thì "chia TB-002 thành 3 đầu mục"
        # chỉ còn lại đúng đầu mục cuối cùng.
        merged = _merge_proposals(
            [],
            [
                {"task_id": "a", "action": "add_subtask", "subtask_content": "một"},
                {"task_id": "a", "action": "add_subtask", "subtask_content": "hai"},
                {"task_id": "a", "action": "add_subtask", "subtask_content": "ba"},
            ],
        )
        assert [item["subtask_content"] for item in merged] == ["một", "hai", "ba"]

    def test_de_xuat_trung_noi_dung_van_gop_lam_mot(self):
        merged = _merge_proposals(
            [{"task_id": "a", "action": "add_subtask", "subtask_content": "một"}],
            [{"task_id": "a", "action": "add_subtask", "subtask_content": "một"}],
        )
        assert len(merged) == 1

    def test_them_viec_con_khong_de_len_lenh_doi_trang_thai(self):
        merged = _merge_proposals(
            [{"task_id": "a", "action": "done"}],
            [{"task_id": "a", "action": "add_subtask", "subtask_content": "một"}],
        )
        assert {item["action"] for item in merged} == {"done", "add_subtask"}

    def test_ba_hanh_dong_cu_van_de_len_nhau(self):
        merged = _merge_proposals(
            [{"task_id": "a", "action": "done"}],
            [{"task_id": "a", "action": "reschedule"}],
        )
        assert merged == [{"task_id": "a", "action": "reschedule"}]


class TestTaskBriefViecCon:
    def test_danh_dau_dong_nao_la_viec_con(self, now, make_task):
        parent = make_task("TB-001")
        child = make_task("TB-001.1", parent_task_id=parent.task_id)
        assert task_brief(parent, now)["is_subtask"] is False
        assert task_brief(child, now)["is_subtask"] is True

    def test_chua_chia_nho_thi_progress_la_null(self, now, make_task):
        # null khác {"done": 0, "total": 0}: một bên là chưa chia, một bên là
        # chia rồi mà chưa xong cái nào
        assert task_brief(make_task("TB-001"), now)["progress"] is None

    def test_co_viec_con_thi_tra_ve_so_dem(self, now, make_task):
        brief = task_brief(make_task("TB-001"), now, (2, 4))
        assert brief["progress"] == {"done": 2, "total": 4}
