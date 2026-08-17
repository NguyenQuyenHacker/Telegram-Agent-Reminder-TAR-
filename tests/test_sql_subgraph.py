"""Subgraph SQL: `validate` thuần Python, và vòng gen_sql · validate · execute · repair.

`validate` là lớp chặn rác không tốn lượt LLM. Nó thay `query_checker` của
pattern SQL agent thông thường, nên phần lớn test ở đây nằm ở đó.

Phép kiểm CÁCH LY DỰ ÁN (nạp hai dự án, hỏi A, khẳng định không có dòng nào của
B) nằm ở `TestCachLyDuAn` và cần DB THẬT — nó tự bỏ qua nếu không nối được.
Không test nào khác bắt được lỗi đó: quên `readonly_tx` là hỏng theo hướng MỞ
và IM LẶNG, role chủ bypass RLS mà không có exception nào.
"""

import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

from TAR_agent.graph_client.subgraph_sql.graph import SqlGraph
from TAR_agent.graph_client.subgraph_sql.nodes import (
    DEFAULT_UNSUPPORTED,
    to_jsonable,
    unsupported_reason,
    validate,
)

LIMIT = 50


def reason(sql: str) -> str | None:
    return validate(sql, LIMIT)[0]


def cleaned(sql: str) -> str:
    return validate(sql, LIMIT)[1]


class TestValidateChoQua:
    def test_select_thuong(self):
        assert reason("SELECT cong_viec FROM cong_viec") is None

    def test_WITH_cte(self):
        assert reason("WITH x AS (SELECT 1) SELECT * FROM x") is None

    def test_dau_cham_phay_CUOI_cau_khong_sao(self):
        assert reason("SELECT 1 FROM cong_viec;") is None

    def test_khong_phan_biet_hoa_thuong(self):
        assert reason("select count(*) from cong_viec") is None


class TestValidateChanRac:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM cong_viec",
            "UPDATE cong_viec SET so_ngay = 0",
            "INSERT INTO cong_viec VALUES (1)",
            "DROP TABLE cong_viec",
        ],
        ids=["delete", "update", "insert", "drop"],
    )
    def test_khong_phai_SELECT_hay_WITH(self, sql: str):
        assert reason(sql) is not None

    def test_hai_cau_lenh(self):
        """Danh sách TRẮNG hai từ khoá, không phải danh sách đen — danh sách đen
        thì luôn thiếu một từ khoá nào đó."""
        assert "MỘT câu lệnh" in reason("SELECT 1; DROP TABLE cong_viec")

    def test_cau_rong(self):
        assert reason("   ") is not None
        assert reason("") is not None

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM data.cong_viec",
            "SELECT * FROM public.project",
            "SELECT * FROM pg_catalog.pg_tables",
            "SELECT * FROM information_schema.columns",
        ],
        ids=["data", "public", "pg_", "information_schema"],
    )
    def test_ten_du_dieu_kien(self, sql: str):
        """`readonly_tx` đã đặt search_path = data nên `FROM cong_viec` là đủ.
        Ba tiền tố còn lại thì tar_ro không có quyền — chặn ở đây rẻ hơn để
        `execute` nổ rồi tốn một lượt `repair`."""
        assert "tên đủ điều kiện" in reason(sql)


class TestValidateDonDep:
    def test_ep_LIMIT_khi_thieu(self):
        """Không có nó thì một `SELECT *` trên file 500 dòng nhét cả bảng vào
        prompt của compose."""
        assert cleaned("SELECT * FROM cong_viec") == f"SELECT * FROM cong_viec LIMIT {LIMIT}"

    def test_KHONG_de_len_LIMIT_model_da_viet(self):
        assert cleaned("SELECT * FROM cong_viec LIMIT 3").endswith("LIMIT 3")

    def test_bo_dau_cham_phay_cuoi(self):
        assert ";" not in cleaned("SELECT 1 FROM cong_viec;")

    def test_dau_cham_phay_TRONG_COMMENT_khong_bi_tinh_la_hai_cau(self):
        """Gỡ comment trước khi kiểm, không thì câu hợp lệ bị từ chối oan."""
        assert reason("SELECT a FROM cong_viec -- ghi chú; có chấm phẩy") is None


class TestToJsonable:
    """ToolNode serialize giá trị trả về thành JSON — một `date` lọt qua là cả
    lượt hỏi chết ở tầng ngoài, xa chỗ gây ra."""

    def test_date_thanh_iso(self):
        assert to_jsonable(date(2026, 6, 1)) == "2026-06-01"

    def test_decimal_thanh_float(self):
        """`SUM()` trên cột INTEGER vẫn ra Decimal."""
        assert to_jsonable(Decimal("47")) == 47.0

    def test_gia_tri_thuong_giu_nguyen(self):
        assert (to_jsonable(None), to_jsonable(5), to_jsonable("x")) == (None, 5, "x")

    def test_kieu_la_thi_ep_ve_chuoi_chu_khong_no(self):
        assert to_jsonable(uuid.uuid4()).count("-") == 4


class _FakeWriter:
    """Thay client LLM sinh SQL. Trả lần lượt các câu đã dựng sẵn."""

    def __init__(self, *replies: str) -> None:
        self.replies, self.calls = list(replies), 0

    async def ainvoke(self, _payload):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else ""

        class Reply:
            content = text

        return Reply()


def run(graph, question="còn bao nhiêu việc chậm"):
    return asyncio.run(
        graph.ainvoke(
            {
                "question": question, "project_id": uuid.uuid4(), "sql": "",
                "rows": [], "columns": [], "error": None, "attempt": 0, "ok": False,
                "unsupported": None,
            }
        )
    )


class TestVongLap:
    """Không chạm DB: `_execute` bị thay để test đúng phần điều khiển luồng."""

    def _graph(self, writer, execute):
        builder = SqlGraph()
        builder.sql_writer = writer
        builder._execute = execute
        return builder.build()

    def test_cau_dat_thi_chay_luon_KHONG_qua_repair(self):
        writer = _FakeWriter("SELECT count(*) FROM cong_viec")

        async def ok(_state):
            return {"ok": True, "error": None, "rows": [{"n": 3}], "columns": ["n"]}

        out = run(self._graph(writer, ok))
        assert writer.calls == 1
        assert out["rows"] == [{"n": 3}]

    def test_validate_truot_thi_repair_MOT_lan_roi_dung(self):
        """Trần `max_sql_attempts = 2`: sinh lần đầu + tối đa một lần sửa."""
        writer = _FakeWriter("DELETE FROM cong_viec", "DROP TABLE cong_viec")

        async def never(_state):
            pytest.fail("câu lệnh trượt validate không được chạy tới execute")

        out = run(self._graph(writer, never))
        assert writer.calls == 2, "một lượt sinh + một lượt sửa, không hơn"
        assert out["rows"] == []

    def test_execute_no_thi_sua_bang_NGUYEN_VAN_loi_cua_postgres(self):
        writer = _FakeWriter("SELECT sai FROM cong_viec", "SELECT cong_viec FROM cong_viec")
        seen: list[str] = []

        async def flaky(state):
            if state["attempt"] == 1:
                return {"ok": False, "error": 'column "sai" does not exist',
                        "rows": [], "columns": []}
            seen.append(state["error"] or "")
            return {"ok": True, "error": None, "rows": [{"c": "A"}], "columns": ["c"]}

        out = run(self._graph(writer, flaky))
        assert out["ok"] and out["rows"] == [{"c": "A"}]

    def test_het_luot_thi_tra_RONG_khong_tra_nua_voi(self):
        """Cùng lý do `grade` trả rỗng ở lượt cuối: một lô dữ liệu sai đưa cho
        compose là mời nó dựng câu trả lời nghe hợp lý mà không gì chứng minh."""
        writer = _FakeWriter("SELECT a FROM cong_viec", "SELECT b FROM cong_viec")

        async def always_fails(_state):
            return {"ok": False, "error": "syntax error", "rows": [], "columns": []}

        out = run(self._graph(writer, always_fails))
        assert out["rows"] == []
        assert writer.calls == 2

    def test_LLM_hong_thi_KHONG_nem_ra_ngoai(self):
        """Hỏng thì MỞ chứ không đóng — exception ở đây giết cả lượt hỏi."""

        class Boom:
            async def ainvoke(self, _payload):
                raise RuntimeError("mất mạng")

        async def never(_state):
            pytest.fail("SQL rỗng không được chạy")

        out = run(self._graph(Boom(), never))
        assert out["rows"] == []


class TestNhanDienLoiTuChoi:
    """`unsupported_reason` thuần Python — không LLM, không DB."""

    def test_cau_SQL_thuong_khong_bi_nham_la_tu_choi(self):
        assert unsupported_reason("SELECT count(*) FROM cong_viec") is None

    def test_chuoi_rong_khong_phai_tu_choi(self):
        """Rỗng là lỗi mạng ở `_gen_sql`, phải đi tiếp vào `validate` như cũ —
        không được biến thành một lời từ chối tự tin gửi cho người dùng."""
        assert unsupported_reason("") is None
        assert unsupported_reason("   ") is None

    def test_lay_duoc_ly_do(self):
        reason = unsupported_reason(
            "KHONG_TRA_LOI_DUOC: bảng chỉ có mốc dự kiến, không có trường trạng thái"
        )
        assert reason == "bảng chỉ có mốc dự kiến, không có trường trạng thái"

    def test_ly_do_xuong_dong_bi_gop_lai_mot_dong(self):
        """Lý do đi thẳng vào câu trả lời Telegram."""
        assert unsupported_reason("KHONG_TRA_LOI_DUOC: không có\n  trường\n\ttrạng thái") == (
            "không có trường trạng thái"
        )

    def test_thieu_dau_hai_cham_van_nhan_ra(self):
        """Model bỏ sót một dấu câu không được làm cả cơ chế im lặng biến mất."""
        assert unsupported_reason("KHONG_TRA_LOI_DUOC bảng không có cột đó") is not None

    def test_bo_trong_ly_do_thi_co_cau_mac_dinh(self):
        """Chuỗi rỗng đưa cho compose là mời nó tự nghĩ ra lý do."""
        assert unsupported_reason("KHONG_TRA_LOI_DUOC:") == DEFAULT_UNSUPPORTED


class TestDuongTuChoi:
    """Model được phép nói "bảng không có trường này" thay vì nặn ra SQL.

    Đây là chốt chặn cho lỗi đã quan sát được: hỏi "% hoàn thành", model viết
    `COUNT(*) FILTER (WHERE ngay_ht <= CURRENT_DATE) / COUNT(*)`, Postgres tính
    thật ra 1.54, và không lớp nào phía sau bắt được — `grounding.check` chỉ
    kiểm con số CÓ trong nguồn hay không, mà 1.54 thì có.
    """

    def _graph(self, writer, execute):
        builder = SqlGraph()
        builder.sql_writer = writer
        builder._execute = execute
        return builder.build()

    def test_tu_choi_thi_KHONG_chay_SQL_va_KHONG_sua(self):
        writer = _FakeWriter("KHONG_TRA_LOI_DUOC: bảng không có trường trạng thái")

        async def never(_state):
            pytest.fail("từ chối rồi thì không được chạy câu lệnh nào")

        out = run(self._graph(writer, never), "dự án đã hoàn thành bao nhiêu phần trăm")
        assert out["unsupported"] == "bảng không có trường trạng thái"
        assert out["rows"] == []
        assert writer.calls == 1, "từ chối không được đá sang repair để nặn lại SQL"

    def test_tu_choi_KHONG_di_qua_validate_thanh_rac_roi_bi_repair(self):
        """Đi qua `validate` thì lời từ chối bị gọi là rác (không mở đầu bằng
        SELECT), `repair` nhận nó như một lỗi cú pháp và ngoan ngoãn viết ra một
        câu SQL — đúng cái hành vi bịa số vừa chặn xong."""
        writer = _FakeWriter(
            "KHONG_TRA_LOI_DUOC: không có % hoàn thành",
            "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE ngay_ht <= CURRENT_DATE) / COUNT(*), 2) FROM cong_viec",
        )

        async def never(_state):
            pytest.fail("không được chạy tới execute")

        out = run(self._graph(writer, never))
        assert writer.calls == 1, "lượt sinh SQL thứ hai = repair đã nuốt lời từ chối"
        assert out["unsupported"]

    def test_tu_choi_o_luot_SUA_cung_duoc_ton_trong(self):
        """Câu đầu trượt validate vì cú pháp, model sửa lại thì mới nhận ra bảng
        không có trường cần thiết."""
        writer = _FakeWriter(
            "DELETE FROM cong_viec",
            "KHONG_TRA_LOI_DUOC: bảng không có cột phần trăm hoàn thành",
        )

        async def never(_state):
            pytest.fail("không được chạy tới execute")

        out = run(self._graph(writer, never))
        assert out["unsupported"] == "bảng không có cột phần trăm hoàn thành"
        assert out["rows"] == []

    def test_cau_hop_le_KHONG_bi_anh_huong(self):
        """Đường cũ phải nguyên vẹn: thêm nhánh từ chối không được làm câu hỏi
        đếm bình thường ngừng chạy."""
        writer = _FakeWriter("SELECT count(*) AS so_luong FROM cong_viec")

        async def ok(_state):
            return {"ok": True, "error": None, "rows": [{"so_luong": 65}],
                    "columns": ["so_luong"]}

        out = run(self._graph(writer, ok))
        assert out["rows"] == [{"so_luong": 65}]
        assert not out.get("unsupported")


class TestCachLyDuAn:
    """Phép kiểm QUAN TRỌNG NHẤT của cả kế hoạch, và là phép kiểm duy nhất bắt
    được nó: nạp hai dự án, hỏi dự án A, khẳng định không có dòng nào của B.

    Quên `readonly_tx` là hỏng theo hướng MỞ và im lặng — `DATABASE_URL` là
    role CHỦ của `data.cong_viec`, mà chủ bảng bỏ qua policy RLS không báo gì.
    Mọi guardrail ở Bước 0 khi đó thành trang trí, và câu trả lời sai dự án
    trông y hệt câu trả lời đúng.

    Cần DB thật nên tự bỏ qua khi không có DATABASE_URL / không nối được.
    """

    @pytest.fixture
    def hai_du_an(self):
        pytest.importorskip("psycopg")
        if not os.getenv("DATABASE_URL"):
            pytest.skip("không có DATABASE_URL")

        from sqlalchemy import text

        from persistence.models import CongViec
        from persistence.pool import get_session

        a, b = uuid.uuid4(), uuid.uuid4()
        doc = uuid.uuid4()
        try:
            with get_session() as session:
                # source_document là khoá ngoại; dựng một dòng tối thiểu.
                session.exec(  # type: ignore[call-overload]
                    text(
                        "INSERT INTO project (project_id, name, normalized_name) "
                        "VALUES (:a, 'TEST A', :na), (:b, 'TEST B', :nb)"
                    ),
                    params={"a": str(a), "b": str(b),
                            "na": f"test a {a.hex}", "nb": f"test b {b.hex}"},
                )
                session.exec(  # type: ignore[call-overload]
                    text(
                        "INSERT INTO source_document (document_id, project_id, "
                        "file_name, file_kind, content_sha256, as_of_date, "
                        "documents, uploaded_by) VALUES (:d, :a, 'test.xlsx', "
                        "'xlsx', 'x', '2026-06-30', '[]'::jsonb, 0)"
                    ),
                    params={"d": str(doc), "a": str(a)},
                )
                for project, name in ((a, "VIEC CUA A"), (b, "VIEC CUA B")):
                    session.add(
                        CongViec(
                            row_id=uuid.uuid4(), document_id=doc, project_id=project,
                            as_of_date=date(2026, 6, 30), cong_viec=name,
                        )
                    )
                session.commit()
        except Exception as exc:
            pytest.skip(f"không nối được DB: {exc}")

        yield a, b

        with get_session() as session:
            session.exec(  # type: ignore[call-overload]
                text("DELETE FROM data.cong_viec WHERE document_id = :d"),
                params={"d": str(doc)},
            )
            session.exec(  # type: ignore[call-overload]
                text("DELETE FROM source_document WHERE document_id = :d"),
                params={"d": str(doc)},
            )
            session.exec(  # type: ignore[call-overload]
                text("DELETE FROM project WHERE project_id IN (:a, :b)"),
                params={"a": str(a), "b": str(b)},
            )
            session.commit()

    def test_hoi_du_an_A_KHONG_thay_dong_nao_cua_B(self, hai_du_an):
        from sqlalchemy import text

        from persistence.pool import readonly_tx

        a, _ = hai_du_an
        # SELECT * KHÔNG có WHERE — đúng thứ model sẽ viết khi nó quên lọc.
        with readonly_tx(a) as session:
            names = [r[0] for r in session.exec(  # type: ignore[call-overload]
                text("SELECT cong_viec FROM cong_viec")
            ).fetchall()]

        assert "VIEC CUA A" in names
        assert "VIEC CUA B" not in names, "RLS không chặn — SQL của LLM thấy dự án khác"

    def test_session_thuong_THAY_ca_hai_day_la_ly_do_phai_co_readonly_tx(self, hai_du_an):
        """Khẳng định chính cái bẫy: role chủ bypass RLS, không có exception nào."""
        from sqlalchemy import text

        from persistence.pool import get_session

        a, _ = hai_du_an
        with get_session() as session:
            names = [r[0] for r in session.exec(  # type: ignore[call-overload]
                text("SELECT cong_viec FROM data.cong_viec")
            ).fetchall()]

        assert {"VIEC CUA A", "VIEC CUA B"} <= set(names)
