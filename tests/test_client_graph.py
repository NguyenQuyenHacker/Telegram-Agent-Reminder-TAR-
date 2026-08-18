"""Cửa rẽ sau `identify_project`, và đường đi của lời TỪ CHỐI khi dữ liệu thiếu.

Phần lớn ở đây chỉ test router và các hàm thuần: dựng `ClientGraph` thật thì
phải có API key Google và một kho Postgres, mà thứ đáng test không cần cả hai.

`_after_identify` không dùng `self`, nên gọi thẳng qua class là đủ và không phải
dựng bốn model LLM trong `__init__`.

`TestHoiPhanTramHoanThanh` là chốt hồi quy cho ca đã quan sát được: hỏi "dự án
hoàn thành bao nhiêu %", bot trả lời "1.54%" — một con số Postgres tính thật từ
`COUNT(*) FILTER (WHERE ngay_ht <= CURRENT_DATE) / COUNT(*)`, tức là tỉ lệ công
việc có MỐC DỰ KIẾN đã qua, không phải tỉ lệ hoàn thành. Bảng không có trường
nào nói việc đã xong hay chưa.

`TestThatSuHoiBot` chạy đúng hai câu hỏi trong tiêu chí nghiệm thu qua LLM thật
+ DB thật. Nó tự bỏ qua khi thiếu `RUN_LIVE_LLM=1` để bộ test thường vẫn chạy
được offline và tất định.
"""

import asyncio
import json
import os
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from TAR_agent.graph_client.graph import ClientGraph
from TAR_agent.graph_client.nodes.agent import (
    number_evidence,
    count_evidence,
    filter_evidence,
)
from TAR_agent.graph_client.nodes.compose import (
    NO_PASSAGE,
    UNSUPPORTED_HEAD,
    render_tool_results,
)
from TAR_agent.graph_client.nodes.identify_project import ProjectPick, ask_what_about
from TAR_agent.graph_client.nodes.reset import reset

_PROJECT_ID = "0171cbea-44e4-5d73-9446-d22d7fc2ae79"

_PROMPTS = Path(__file__).resolve().parents[1] / "TAR_agent" / "utils" / "prompts"


def _route(state: dict) -> str:
    return ClientGraph._after_identify(None, state)


def _route_agent(state: dict) -> str:
    return ClientGraph._after_agent(None, state)


class TestSauIdentifyProject:
    def test_co_du_an_va_co_cau_hoi_thi_di_tra(self):
        assert _route({"project_id": _PROJECT_ID, "reply": None}) == "agent"

    def test_khong_chot_duoc_du_an_thi_ra_thang(self):
        assert _route({"project_id": None, "reply": "Bạn muốn hỏi dự án nào?"}) == "respond"

    def test_chot_du_an_nhung_chua_co_cau_hoi_thi_KHONG_tra(self):
        """Luật 8. Nhìn mỗi `project_id` là đá thẳng câu "cho mình hỏi về X đi"
        vào vòng tra cứu: hai nhánh tra chạy trên một câu không có ý hỏi nào, rồi
        compose đổ cả dự án ra màn hình — hai chục giây cho một câu không ai hỏi."""
        state = {"project_id": _PROJECT_ID, "reply": ask_what_about("Bo_tai_chinh")}
        assert _route(state) == "respond"

    def test_loi_LLM_thi_ra_thang_chu_khong_di_tra(self):
        assert _route({"project_id": None, "reply": None, "error": "llm_failed"}) == "respond"


class TestNhanhHoiLai:
    def test_giu_du_an_de_luot_sau_hoi_coc_loc_van_hieu(self):
        """Hỏi lại xong mà xoá luôn dự án thì người dùng trả lời "tiến độ thế
        nào" ở lượt sau lại bị hỏi tên dự án lần nữa."""
        state = {
            "project_id": _PROJECT_ID,
            "project_name": "Bo_tai_chinh",
            "reply": ask_what_about("Bo_tai_chinh"),
        }
        assert _route(state) == "respond"
        assert state["project_id"] == _PROJECT_ID

    def test_reply_duoc_don_dau_moi_luot(self):
        """`reply` là thứ cắt vòng tra cứu. Không dọn thì một lượt hỏi lại làm
        MỌI lượt sau cũng ra thẳng, bot không tra gì nữa."""
        assert reset({"reply": "Bạn muốn biết gì?", "messages": []})["reply"] is None

    def test_model_quen_viet_cau_hoi_lai_thi_code_van_co_cau_de_gui(self):
        pick = ProjectPick(project_id=_PROJECT_ID, project_name="A", needs_question=True)
        assert pick.reply is None
        assert "A" in (pick.reply or ask_what_about("A"))

    def test_mac_dinh_la_di_tra(self):
        """`needs_question` thiếu trong output của LLM thì lượt đó phải chạy
        bình thường, không phải im lặng hỏi lại."""
        assert ProjectPick(project_id=_PROJECT_ID).needs_question is False


def _tool_message(payload: dict) -> ToolMessage:
    """Đúng hình dạng `_retrieve` sinh ra: content là JSON đã serialize."""
    return ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id="x")


class _Doc(SimpleNamespace):
    """Đủ mặt thuộc tính mà `_search_docs` đọc, không hơn."""


def _doc(content: str) -> _Doc:
    return _Doc(
        content=content,
        file_name="TiendoT8.xlsx",
        as_of_date=date(2026, 8, 15),
        heading_path="",
    )


def _run_retrieve_all(monkeypatch, *, table, docs) -> dict:
    """Chạy `retrieve_all` với hai subgraph giả.

    `table` nhận một dict (state trả về của SQL_GRAPH) hoặc một Exception để giả
    cảnh nhánh đó nổ.
    """
    from TAR_agent.graph_client.tools import retrieval as mod

    class _Fake:
        def __init__(self, ket_qua):
            self.ket_qua = ket_qua

        async def ainvoke(self, _state):
            if isinstance(self.ket_qua, BaseException):
                raise self.ket_qua
            return self.ket_qua

    monkeypatch.setattr(mod, "SQL_GRAPH", _Fake(table))
    monkeypatch.setattr(mod, "SEARCH_GRAPH", _Fake({"docs": docs, "attempt": 1}))
    return asyncio.run(mod.retrieve_all("câu hỏi", uuid.uuid4()))


def _payload_table(*cong_viec: str) -> dict:
    """Payload nhánh bảng với mỗi dòng một tên công việc."""
    return {
        "table": "ok",
        "status": "ok",
        "sql": "SELECT 1",
        "columns": ["cong_viec", "don_vi"],
        "rows": [{"cong_viec": ten, "don_vi": "Cục CNTT"} for ten in cong_viec],
    }


class TestSauAgent:
    """`_after_agent` chỉ đọc `agent_action`. Suy lại từ `messages` ở router là
    hai chỗ cùng quyết một việc, và chúng lệch nhau ở đúng ca hiếm."""

    def test_can_tra_thi_di_retrieve(self):
        assert _route_agent({"agent_action": "retrieve", "query": "x"}) == "retrieve"

    def test_tra_loi_thang_thi_ra_respond(self):
        assert _route_agent({"agent_action": "answer", "reply": "Cảm ơn bạn"}) == "respond"

    def test_chon_loc_xong_thi_sang_compose(self):
        assert _route_agent({"agent_action": "compose", "evidence": "..."}) == "compose"

    def test_thieu_agent_action_thi_MAC_DINH_di_tra(self):
        """Ra thẳng `respond` khi không rõ là gửi một câu trả lời rỗng."""
        assert _route_agent({}) == "retrieve"
        assert _route_agent({"agent_action": "lung tung"}) == "retrieve"

    def test_agent_action_duoc_don_dau_moi_luot(self):
        """Giữ "compose" của lượt trước thì lượt sau LLM hỏng là router đi thẳng
        sang compose với `evidence` của câu hỏi cũ."""
        sach = reset({"agent_action": "compose", "evidence": "cũ", "messages": []})
        assert sach["agent_action"] == ""
        assert sach["evidence"] == ""


class TestHaiSubgraphChungMotNode:
    """Hồi quy cho `MultipleSubgraphsError`.

    `retrieve_all` gọi hai subgraph SONG SONG trong node `retrieve`. Subgraph compile
    không khai checkpointer thì lúc chạy nó THỪA KẾ checkpointer của graph cha
    qua config, và LangGraph cấm hai subgraph có checkpointer cùng chạy trong
    một node — cả lượt hỏi mất nhánh tài liệu, chỉ còn dòng ERROR trong log.

    Khai `checkpointer=False` là thứ chặn điều đó, và nó không mất gì: hai vòng
    đó là phép tính bên trong một lời gọi tra cứu, không ai resume chúng.
    """

    def test_ca_hai_subgraph_tu_choi_checkpointer_cua_graph_cha(self):
        from TAR_agent.graph_client.subgraph.graph import SEARCH_GRAPH
        from TAR_agent.graph_client.subgraph_sql.graph import SQL_GRAPH

        assert SEARCH_GRAPH.checkpointer is False
        assert SQL_GRAPH.checkpointer is False


class TestDanhMucBangChung:
    def test_danh_so_chay_suot_qua_nhieu_luot_tra(self):
        """Vòng revise tra thêm một lô. Đánh số lại từ đầu ở lô thứ hai thì
        "giữ D1" không còn chỉ vào đúng một dòng nào."""
        danh_muc = number_evidence(
            [
                _tool_message(_payload_table("Việc A", "Việc B")),
                _tool_message(_payload_table("Việc C")),
            ]
        )
        assert "D1. Việc A" in danh_muc
        assert "D2. Việc B" in danh_muc
        assert "D3. Việc C" in danh_muc

    def test_doan_tai_lieu_danh_so_rieng(self):
        danh_muc = number_evidence(
            [
                _tool_message(
                    {
                        **_payload_table("Việc A"),
                        "passages": [
                            {
                                "content": "Nội dung đoạn",
                                "file_name": "T8.xlsx",
                                "as_of_date": "2026-08-15",
                                "heading_path": "",
                            }
                        ],
                    }
                )
            ]
        )
        assert "D1. Việc A" in danh_muc
        assert "P1. T8.xlsx" in danh_muc

    def test_khoi_tu_choi_KHONG_duoc_danh_so(self):
        """Nó không phải dữ liệu để chọn mà là lý do vì sao không có dữ liệu —
        và nó luôn đi tiếp tới compose, không qua tay ai."""
        danh_muc = number_evidence(
            [_tool_message({"table": "unsupported", "table_reason": "không có cột trạng thái"})]
        )
        assert UNSUPPORTED_HEAD in danh_muc
        assert "D1" not in danh_muc and "P1" not in danh_muc

    def test_count_evidence(self):
        assert count_evidence([_tool_message(_payload_table("A", "B"))]) == (2, 0)
        assert count_evidence([_tool_message({"table": "empty"})]) == (0, 0)


class TestLocBangChung:
    """Toàn bộ điểm của node `agent`: 27 dòng khớp mờ vào, đúng phần trả lời
    được câu hỏi ra."""

    def test_chi_giu_dong_duoc_chon(self):
        giu = filter_evidence(
            [_tool_message(_payload_table("Việc A", "Việc B", "Việc C"))],
            keep_rows=[1, 3],
            keep_passages=[],
        )
        block = render_tool_results(giu)
        assert "Việc A" in block and "Việc C" in block
        assert "Việc B" not in block

    def test_chi_so_ngoai_khoang_thi_bo_qua_chu_khong_nem(self):
        """Model bịa ra `D40` cho một lô 3 dòng là chuyện có thật."""
        giu = filter_evidence(
            [_tool_message(_payload_table("Việc A", "Việc B"))],
            keep_rows=[2, 40, 0, -1],
            keep_passages=[7],
        )
        block = render_tool_results(giu)
        assert "Việc B" in block and "Việc A" not in block

    def test_cat_xuyen_qua_nhieu_luot_tra(self):
        giu = filter_evidence(
            [
                _tool_message(_payload_table("Việc A", "Việc B")),
                _tool_message(_payload_table("Việc C")),
            ],
            keep_rows=[3],
            keep_passages=[],
        )
        block = render_tool_results(giu)
        assert "Việc C" in block
        assert "Việc A" not in block and "Việc B" not in block

    def test_giu_nguyen_van_chu_trong_dong_duoc_giu(self):
        """Lọc bằng CHỈ SỐ chứ không bằng chữ: model không có cơ hội sửa một con
        số hay rụng một chữ trong tên công việc."""
        ten = "Ký hợp đồng tư vấn Khảo sát, tư vấn lập Báo cáo NCKT(02 tư vấn)"
        giu = filter_evidence([_tool_message(_payload_table(ten))], [1], [])
        assert ten in render_tool_results(giu)

    def test_loi_tu_choi_di_qua_duoc_du_khong_ai_chon_no(self):
        payload = {**_payload_table("Việc A"), "table": "unsupported",
                   "table_reason": "không có cột trạng thái"}
        giu = filter_evidence([_tool_message(payload)], keep_rows=[], keep_passages=[])
        assert UNSUPPORTED_HEAD in render_tool_results(giu)

    def test_message_bi_loc_sach_thi_bo_han(self):
        """Payload rỗng đi tới `render_tool_results` chỉ tạo một khối trắng."""
        assert filter_evidence([_tool_message(_payload_table("Việc A"))], [], []) == []

    def test_payload_khong_doc_duoc_thi_giu_nguyen(self):
        """Nó không được đánh số nên model không chọn được, mà vứt đi thì mất
        một kết quả tra."""
        la = ToolMessage(content="không phải JSON", tool_call_id="x")
        assert filter_evidence([la], [], []) == [la]


class TestHoiPhanTramHoanThanh:
    """CA 1 của tiêu chí nghiệm thu, phần tất định.

    Kiểm phần code chạy được không cần LLM: lời từ chối của tầng SQL có tới
    được prompt của `compose` nguyên vẹn không, và nó có mang theo con số nào
    không. Phần "model viết ra câu gì" nằm ở `TestThatSuHoiBot`.
    """

    def test_loi_tu_choi_toi_duoc_compose(self):
        block = render_tool_results(
            [
                _tool_message(
                    {
                        "status": "unsupported",
                        "reason": "bảng chỉ có mốc thời gian dự kiến, không có "
                        "trường trạng thái hay phần trăm hoàn thành",
                    }
                )
            ]
        )
        assert UNSUPPORTED_HEAD in block
        assert "phần trăm hoàn thành" in block

    def test_KHONG_bi_bao_thanh_kho_chua_co_tai_lieu(self):
        """Nuốt mất khối `unsupported` thì `blocks` rỗng -> compose rơi vào
        NO_PASSAGE -> trả lời "kho chưa có tài liệu nào nói về việc này". Sai,
        và còn mời người dùng đi nạp thêm file trong khi vấn đề là bảng không
        có cột đó."""
        block = render_tool_results(
            [_tool_message({"status": "unsupported", "reason": "không có cột trạng thái"})]
        )
        assert block != NO_PASSAGE

    def test_khoi_tu_choi_KHONG_mang_theo_con_so_nao(self):
        """`grounding.check` gom mọi số trong nguồn thành tập số HỢP LỆ. Một con
        số lọt vào khối từ chối là tự cấp phép cho compose viết nó ra."""
        import re

        block = render_tool_results(
            [_tool_message({"status": "unsupported", "reason": "không có trường trạng thái"})]
        )
        assert not re.search(r"\d", block), f"khối từ chối lọt con số: {block!r}"

    def test_tra_cuu_giu_rieng_loi_tu_choi_cua_nhanh_bang(self, monkeypatch):
        """Gộp vào `status: "empty"` là mất hẳn phần phân biệt: "không có dòng
        nào khớp" và "không có cột để trả lời" dẫn tới hai câu trả lời khác
        nhau."""
        out = _run_retrieve_all(
            monkeypatch,
            table={"rows": [], "sql": "", "attempt": 1,
                  "unsupported": "bảng không có trường trạng thái"},
            docs=[],
        )
        assert out["table"] == "unsupported"
        assert out["table_reason"] == "bảng không có trường trạng thái"
        assert "rows" not in out


class TestTraCuuGopHaiNhanh:
    """Hai nhánh chạy song song trong MỘT payload — mọi khẳng định ở đây đều về
    chuyện một nhánh hỏng KHÔNG được kéo nhánh kia chết theo. Đó là toàn bộ lý
    do gộp hai tool lại."""

    def test_nhanh_bang_tu_choi_van_giu_nguyen_doan_tai_lieu(self, monkeypatch):
        """Ca hồi quy của bản gộp: `render_tool_results` từng `continue` ngay sau
        khối từ chối, nên payload vừa có lời từ chối vừa có đoạn tài liệu thì
        phần tài liệu bị vứt — đúng phần đang cứu được lượt hỏi đó."""
        out = _run_retrieve_all(
            monkeypatch,
            table={"rows": [], "sql": "", "attempt": 1, "unsupported": "không có cột tiến độ"},
            docs=[_doc("Khảo sát thực tế tại các đơn vị")],
        )
        assert out["table"] == "unsupported"
        assert len(out["passages"]) == 1
        assert out["status"] == "ok", "một nhánh có dữ liệu thì chưa phải là rỗng"

        block = render_tool_results([_tool_message(out)])
        assert UNSUPPORTED_HEAD in block
        assert "Khảo sát thực tế tại các đơn vị" in block

    def test_nhanh_bang_rong_van_tra_loi_duoc_bang_tai_lieu(self, monkeypatch):
        """Đúng ca trong log: SQL trả 0 dòng vì viết hụt một điều kiện. Bản cũ
        dừng ở đó vì model đã chọn tool SQL; bản gộp còn nhánh tài liệu đỡ."""
        out = _run_retrieve_all(
            monkeypatch,
            table={"rows": [], "sql": "SELECT 1", "attempt": 1, "unsupported": None},
            docs=[_doc("Báo cáo kết quả khảo sát")],
        )
        assert out["status"] == "ok"
        assert out["table"] == "empty"
        assert out["docs"] == "ok"
        assert "Báo cáo kết quả khảo sát" in render_tool_results([_tool_message(out)])

    def test_ca_hai_nhanh_rong_moi_la_empty(self, monkeypatch):
        """`_all_tools_empty` dựa vào đúng khoá này để tắt vòng tra lại. Đặt
        "empty" khi mới một nhánh cạn là cắt mất lượt còn cứu được."""
        out = _run_retrieve_all(
            monkeypatch,
            table={"rows": [], "sql": "", "attempt": 1, "unsupported": None},
            docs=[],
        )
        assert out["status"] == "empty"

    def test_mot_nhanh_nem_thi_nhanh_kia_van_ve(self, monkeypatch):
        """`asyncio.gather` mặc định huỷ cả nhóm khi một task ném — mất cả lượt
        hỏi vì một nhánh hỏng. `return_exceptions=True` là thứ chặn điều đó."""
        out = _run_retrieve_all(
            monkeypatch,
            table=RuntimeError("Postgres đứt kết nối"),
            docs=[_doc("Biên bản nghiệm thu kết quả khảo sát")],
        )
        assert out["table"] == "error"
        assert out["status"] == "ok"
        assert len(out["passages"]) == 1


class TestPromptKhongConDayPhepTinhSai:
    """Ví dụ trong prompt là thứ model bắt chước sát nhất. Ví dụ `ty_le_cham`
    dạy đúng phép tính đã sinh ra "1.54%", nên nó phải biến mất và không được
    ai vô tình chép lại."""

    def _read(self, name: str) -> str:
        return (_PROMPTS / f"client_system/{name}.md").read_text(encoding="utf-8")

    def test_gen_sql_khong_con_vi_du_ty_le_cham(self):
        assert "ty_le_cham" not in self._read("gen_sql")

    def test_gen_sql_khong_con_vi_du_chia_COUNT_FILTER_theo_ngay(self):
        text = self._read("gen_sql")
        assert "FILTER (WHERE ngay_ht" not in text, (
            "prompt còn một ví dụ quy đổi ngày dự kiến thành tỉ lệ — đó chính là "
            "khuôn model đã chép để ra 1.54%"
        )

    def test_gen_sql_noi_ro_day_la_ke_hoach_du_kien(self):
        text = self._read("gen_sql").lower()
        assert "kế hoạch dự kiến" in text
        assert "không có" in text and "phần trăm hoàn thành" in text

    def test_gen_sql_co_duong_tu_choi(self):
        assert "KHONG_TRA_LOI_DUOC" in self._read("gen_sql")

    def test_compose_co_luat_cho_unsupported(self):
        assert UNSUPPORTED_HEAD in self._read("compose"), (
            "nhãn khối trong compose.py và trong compose.md phải khớp nguyên văn, "
            "không thì luật 4b không bao giờ khớp với thứ model nhìn thấy"
        )

    def test_compose_cam_gan_nhan_tien_do(self):
        text = self._read("compose").lower()
        assert "% hoàn thành" in text and "tiến độ" in text

    def test_compose_co_cho_dat_outline(self):
        """`load_prompt` thay `{{OUTLINE}}` bằng chuỗi. Thiếu chỗ đặt thì dàn ý
        của bước chọn lọc bị vứt lặng lẽ, không có lỗi nào nổ."""
        assert "{{OUTLINE}}" in self._read("compose")

    def test_agent_decide_mac_dinh_la_tra(self):
        """Phân vân mà trả lời chay là bịa ra nội dung tài liệu."""
        text = self._read("agent_decide")
        assert "{{QUESTION}}" in text
        assert "Mặc định là TRA" in text

    def test_agent_select_day_giu_du_danh_sach(self):
        """Luật dễ hỏng nhất: model thấy 27 dòng thì muốn rút gọn cho đẹp, mà
        câu hỏi "gồm những công việc nào" thì cắt bớt là trả lời sai."""
        text = self._read("agent_select")
        assert "{{QUESTION}}" in text
        assert "liệt kê tất cả" in text


_LIVE = os.getenv("RUN_LIVE_LLM") == "1"


@pytest.mark.skipif(not _LIVE, reason="cần RUN_LIVE_LLM=1, GOOGLE_API_KEY và DB thật")
class TestThatSuHoiBot:
    """TIÊU CHÍ NGHIỆM THU, chạy qua LLM thật + Neon thật.

    Tốn tiền và cần mạng nên tắt mặc định. Đây là phép kiểm DUY NHẤT trả lời
    được câu hỏi thật sự đáng quan tâm — model có chịu từ chối không — vì mọi
    test khác đều tự dựng sẵn câu trả lời của model.
    """

    def _chay(self, *cau_hoi: str) -> list[str]:
        """Hỏi lần lượt trên CÙNG một thread, trong CÙNG một event loop.

        Một `asyncio.run` cho cả kịch bản, không phải mỗi lượt một cái: kênh
        grpc của client Gemini gắn với event loop đã tạo ra nó, nên lượt thứ
        hai chạy trên loop mới sẽ hỏng và graph trả về event `client_failed`.
        Lỗi đó không làm test đỏ một cách trung thực — nó chỉ làm câu trả lời
        rỗng, mà chuỗi rỗng thì thoả mọi khẳng định dạng "không chứa số %".
        Đó cũng đúng hình dạng thật: server chạy một loop duy nhất.
        """
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from TAR_agent.graph_client.graph import build_client_graph

        cfg = {"configurable": {"thread_id": f"test-{uuid.uuid4().hex}"}}

        async def scenario() -> list[str]:
            bot = build_client_graph(MemorySaver())
            answers: list[str] = []
            for text in cau_hoi:
                out = await bot.ainvoke({"chat_history": [HumanMessage(text)]}, cfg)
                events = out.get("outbox") or []
                # Sự cố ở tầng graph đi ra bằng `client_failed`, không có khoá
                # `text` — bắt Ở ĐÂY chứ không để nó thành chuỗi rỗng lặng lẽ.
                assert events, f"lượt {text!r} không gửi gì cả"
                kinds = [e["kind"] for e in events]
                assert kinds == ["answer"], f"lượt {text!r} ra event {kinds}: {events}"
                answers.append("\n".join(str(e["data"]["text"]) for e in events))
            return answers

        return asyncio.run(scenario())

    def test_ca1_hoi_phan_tram_thi_KHONG_ra_con_so_phan_tram(self):
        import re

        (answer,) = self._chay("Dự án đã hoàn thành khoảng bao nhiêu phần trăm?")
        print(f"\n[CA 1] {answer}\n")

        assert answer.strip(), "câu trả lời rỗng — mọi khẳng định phía dưới thành vô nghĩa"
        so_phan_tram = re.findall(r"\d+(?:[.,]\d+)?\s*%", answer)
        assert not so_phan_tram, f"vẫn ra con số phần trăm: {so_phan_tram} | {answer!r}"
        assert re.search(
            r"không xác định|không có|chưa có|không đủ", answer, re.IGNORECASE
        ), f"không nói rõ là không xác định được: {answer!r}"

    def test_ca2_hai_luot_lien_tiep_KHONG_mau_thuan(self):
        """Cùng MỘT thread_id, hỏi tuần tự — đúng kịch bản đã sinh ra mâu thuẫn.

        Mâu thuẫn cũ: lượt 1 đưa "1.54%", lượt 2 nói "kho chưa có tài liệu nào
        nói về việc này". Sửa ở gốc (lượt 1 không còn đưa số nào) thì hai câu tự
        khớp nhau mà compose KHÔNG cần nhìn thấy lịch sử hội thoại — đó là bằng
        chứng mục 4 chưa cần làm gấp.

        Test này khẳng định đúng thứ đã đo được là ổn định qua nhiều lần chạy:
        KHÔNG lượt nào ra con số phần trăm, và cả hai lượt đều là lời từ chối.

        Nó CỐ Ý không khẳng định lượt 2 dùng đúng câu chữ "không có trường ...".
        Định tuyến tool do LLM chọn (`temperature 0.3`), và khi nó chỉ gọi
        `search_docs`, compose không có tín hiệu nào để biết bảng thiếu cột —
        nó rơi vào luật 4 và nói "Kho chưa có tài liệu nào nói về việc này".
        Đo được ~1/4 lần. Sai về QUY KẾT (đổ cho thiếu tài liệu, trong khi nạp
        thêm file cũng không giải quyết được), không sai về con số. Sửa chỗ đó
        phải sửa định tuyến ở `client_system/system.md` — ngoài phạm vi đợt này.
        """
        import re

        luot1, luot2 = self._chay(
            "Dự án đã hoàn thành khoảng bao nhiêu phần trăm?",
            "Bạn đánh giá tiến độ dựa trên gì?",
        )
        print(f"\n[CA 2 · lượt 1] {luot1}\n\n[CA 2 · lượt 2] {luot2}\n")

        assert luot1.strip() and luot2.strip(), "có lượt trả lời rỗng"

        # Điều kiện TRƯỢT của cả đợt sửa: bất kỳ con số phần trăm nào.
        for ten, cau in (("lượt 1", luot1), ("lượt 2", luot2)):
            so = re.findall(r"\d+(?:[.,]\d+)?\s*%", cau)
            assert not so, f"{ten} ra số phần trăm {so}: {cau!r}"

        # Nhất quán: cả hai đều phải là lời từ chối. Mâu thuẫn cũ là một lượt
        # khẳng định con số còn lượt kia phủ nhận có nguồn — hình dạng đó chỉ
        # xuất hiện khi MỘT trong hai lượt không phải lời từ chối.
        tu_choi = re.compile(r"không xác định|không có|chưa có|không đủ", re.IGNORECASE)
        assert tu_choi.search(luot1), f"lượt 1 không phải lời từ chối: {luot1!r}"
        assert tu_choi.search(luot2), f"lượt 2 không phải lời từ chối: {luot2!r}"
