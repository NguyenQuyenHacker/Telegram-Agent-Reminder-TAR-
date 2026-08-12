"""Phần thuần của tầng tool: bản rút gọn đưa cho LLM, kiểm tham số, gộp đề xuất.

Không đụng DB — mọi hàm ở đây nhận sẵn Task dựng trong bộ nhớ hoặc dict đề xuất.
"""

from datetime import date

import pytest

from persistence.models.task import Priority, TaskStatus
from persistence.proc.tasks import _ilike_pattern
from reminder_agent.graph import _merge_proposals, _parse_iso_date
from reminder_agent.utils.tools.query_tasks import (
    _parse_date,
    _parse_priority,
    _parse_status,
)
from reminder_agent.utils.tools.task_view import task_brief

# now của conftest là 2026-07-17
TODAY = date(2026, 7, 17)


class TestTaskBrief:
    def test_tra_ve_muc_uu_tien_hieu_luc_chu_khong_phai_cot_tho(self, now, make_task):
        # Cùng công thức với rổ trong tin nhắc gộp: việc quá hạn là gấp, dù cột
        # priority vẫn ghi "normal" theo mức gốc của báo cáo.
        task = make_task("A-001", due_date=date(2026, 7, 10))
        assert task.priority == Priority.normal
        assert task_brief(task, now)["priority"] == "urgent"

    def test_han_con_xa_thi_van_la_binh_thuong(self, now, make_task):
        task = make_task("A-002", due_date=date(2026, 12, 31))
        assert task_brief(task, now)["priority"] == "normal"

    def test_tinh_san_thu_va_so_ngay_con_lai(self, now, make_task):
        brief = task_brief(make_task("A-003", due_date=date(2026, 7, 20)), now)
        assert brief["due_weekday"] == "Thứ Hai"
        assert brief["days_left"] == 3

    def test_qua_han_thi_so_ngay_con_lai_am(self, now, make_task):
        brief = task_brief(make_task("A-004", due_date=date(2026, 7, 10)), now)
        assert brief["days_left"] == -7

    def test_chua_co_han_thi_ba_truong_ngay_deu_rong(self, now, make_task):
        brief = task_brief(make_task("A-005", due_date=None), now)
        assert brief["due_date"] is None
        assert brief["due_weekday"] is None
        assert brief["days_left"] is None

    def test_khong_lo_task_id_ra_cho_llm(self, now, make_task):
        # LLM nhắc việc bằng mã người dùng gõ lại được, không phải bằng băm
        brief = task_brief(make_task("A-006"), now)
        assert "task_id" not in brief
        assert brief["code"] == "A-006"


class TestParseThamSo:
    def test_doc_duoc_gia_tri_le_hoa_thuong_va_khoang_trang(self):
        assert _parse_status(" PENDING ") == TaskStatus.pending
        assert _parse_priority("Urgent") == Priority.urgent

    def test_bo_trong_thi_khong_loc(self):
        assert _parse_status(None) is None
        assert _parse_priority("") is None
        assert _parse_date(None, "due_from") is None

    @pytest.mark.parametrize(
        "parse, value",
        [
            (_parse_status, "hoàn thành"),
            (_parse_priority, "cao"),
            (lambda value: _parse_date(value, "due_from"), "20/7"),
        ],
    )
    def test_gia_tri_sai_thi_bao_loi_chu_khong_tra_bang_rong(self, parse, value):
        # Nuốt lỗi ở đây là LLM đọc ra "không có việc nào" và nói y hệt với người dùng
        with pytest.raises(ValueError):
            parse(value)


class TestMerGopDeXuat:
    def test_hai_de_xuat_cung_mot_viec_thi_giu_cai_sau(self):
        merged = _merge_proposals(
            [{"task_id": "a", "action": "done"}],
            [{"task_id": "a", "action": "reschedule"}],
        )
        assert merged == [{"task_id": "a", "action": "reschedule"}]

    def test_viec_khac_nhau_thi_giu_het_va_dung_thu_tu(self):
        merged = _merge_proposals(
            [{"task_id": "a", "action": "done"}],
            [{"task_id": "b", "action": "cancel"}],
        )
        assert [item["task_id"] for item in merged] == ["a", "b"]


class TestKiemNgayCuaDeXuat:
    def test_ngay_hong_hoac_thieu_deu_ra_none(self):
        # _apply_reschedule dựa vào đây để KHÔNG gọi set_due_date(None) — gọi là
        # xoá luôn hạn cũ trong khi bảng xác nhận vừa hứa là "đổi hạn"
        assert _parse_iso_date(None) is None
        assert _parse_iso_date("20/7/2026") is None
        assert _parse_iso_date("2026-07-20") == date(2026, 7, 20)


class TestMauTimKiem:
    def test_vo_hieu_ky_tu_dai_dien_nguoi_dung_go(self):
        # Không escape thì "100%" thành mẫu khớp mọi thứ và tool quét cả bảng
        assert _ilike_pattern("  100%_x  ") == r"%100\%\_x%"

    def test_chuoi_thuong_giu_nguyen(self):
        assert _ilike_pattern("báo cáo") == "%báo cáo%"
