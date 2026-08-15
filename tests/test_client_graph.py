"""Cửa rẽ sau `identify_project` — chỗ quyết định một lượt có tra cứu hay không.

Chỉ test router và các hàm thuần: dựng `ClientGraph` thật thì phải có API key
Google và một kho Postgres, mà thứ đáng test ở đây không cần cả hai.

`_after_identify` không dùng `self`, nên gọi thẳng qua class là đủ và không phải
dựng bốn model LLM trong `__init__`.
"""

from TAR_agent.graph_client.graph import ClientGraph
from TAR_agent.graph_client.nodes.identify_project import ProjectPick, ask_what_about
from TAR_agent.graph_client.nodes.reset import reset

_PROJECT_ID = "0171cbea-44e4-5d73-9446-d22d7fc2ae79"


def _route(state: dict) -> str:
    return ClientGraph._after_identify(None, state)


class TestSauIdentifyProject:
    def test_co_du_an_va_co_cau_hoi_thi_di_tra(self):
        assert _route({"project_id": _PROJECT_ID, "reply": None}) == "agent"

    def test_khong_chot_duoc_du_an_thi_ra_thang(self):
        assert _route({"project_id": None, "reply": "Bạn muốn hỏi dự án nào?"}) == "respond"

    def test_chot_du_an_nhung_chua_co_cau_hoi_thi_KHONG_tra(self):
        """Luật 8. Nhìn mỗi `project_id` là đá thẳng câu "cho mình hỏi về X đi"
        vào vòng tra cứu: agent phải tự bịa truy vấn, rồi compose đổ cả dự án ra
        màn hình — hai chục giây cho một câu không ai hỏi."""
        state = {"project_id": _PROJECT_ID, "reply": ask_what_about("Bo_tai_chinh")}
        assert _route(state) == "respond"

    def test_loi_LLM_thi_ra_thang_chu_khong_vao_agent(self):
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
