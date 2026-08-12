from datetime import date

from app.core.reminder_digest import BUCKET_ORDER, NORMAL, OVERDUE, URGENT, group_by_bucket
from persistence.models.task import Priority

# now của conftest là 2026-07-17
TODAY = date(2026, 7, 17)


def test_danh_sach_rong_van_du_ba_ro(now):
    buckets = group_by_bucket([], now)
    assert list(buckets) == list(BUCKET_ORDER)
    assert all(tasks == [] for tasks in buckets.values())


def test_qua_han_tach_khoi_uu_tien(now, make_task):
    overdue = make_task("A-001", due_date=date(2026, 7, 10))
    buckets = group_by_bucket([overdue], now)
    # maybe_escalate cũng nâng việc quá hạn lên urgent, nhưng rổ quá hạn phải
    # thắng: người dùng cần phân biệt "gấp" với "đã trễ"
    assert buckets[OVERDUE] == [overdue]
    assert buckets[URGENT] == []


def test_han_hom_nay_la_uu_tien_chua_qua_han(now, make_task):
    task = make_task("A-002", due_date=TODAY)
    buckets = group_by_bucket([task], now)
    assert buckets[URGENT] == [task]
    assert buckets[OVERDUE] == []


def test_sap_toi_han_duoc_nang_len_uu_tien(now, make_task):
    task = make_task("A-003", due_date=date(2026, 7, 19))
    assert group_by_bucket([task], now)[URGENT] == [task]


def test_con_xa_thi_nam_ro_thuong(now, make_task):
    task = make_task("A-004", due_date=date(2026, 12, 31))
    assert group_by_bucket([task], now)[NORMAL] == [task]


def test_viec_gap_luon_o_ro_uu_tien_du_han_con_xa(now, make_task):
    task = make_task("A-005", due_date=date(2026, 12, 31), priority=Priority.urgent)
    assert group_by_bucket([task], now)[URGENT] == [task]


def test_chua_co_han_thi_ve_ro_thuong(now, make_task):
    task = make_task("A-006", due_date=None)
    buckets = group_by_bucket([task], now)
    assert buckets[NORMAL] == [task]
    assert buckets[OVERDUE] == []


def test_giu_nguyen_thu_tu_dau_vao_trong_tung_ro(now, make_task):
    first = make_task("A-007", due_date=date(2026, 7, 1))
    second = make_task("A-008", due_date=date(2026, 7, 5))
    assert group_by_bucket([first, second], now)[OVERDUE] == [first, second]
