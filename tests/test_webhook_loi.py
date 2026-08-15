"""Tầng webhook hỏng thì admin PHẢI biết — không được im lặng.

Đây là nhóm test sinh ra từ những lần hỏng thật khi thử trên Telegram:

    psycopg.OperationalError: SSL connection has been closed unexpectedly
    psycopg.OperationalError: the connection is closed
    TelegramConflictError: terminated by other getUpdates request

Điểm chung: lỗi bật ra ở TẦNG NGOÀI graph. aiogram bắt, ghi log, rồi thôi —
admin gửi file xong ngồi chờ mãi không thấy hồi âm nào. Đứng từ phía người dùng
thì "bot chết" và "bot đang đọc file 40 trang" trông y hệt nhau.

Nên quy tắc ở đây: MỌI đường ra khỏi handler đều phải nói một câu gì đó.
"""

import asyncio
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

import pytest

webhooks = import_module("app.routers.webhooks")

CHAT_ID = 6_607_323_870
ADMIN_ID = 6_607_323_870




@dataclass
class FakeUser:
    id: int = ADMIN_ID


@dataclass
class FakeChat:
    id: int = CHAT_ID


@dataclass
class FakeMessage:
    text: str | None = None
    caption: str | None = None
    document: Any = None
    chat: FakeChat = field(default_factory=FakeChat)
    from_user: FakeUser | None = field(default_factory=FakeUser)

    def __post_init__(self):
        self.markup_da_go = False

    async def edit_reply_markup(self, reply_markup=None):
        self.markup_da_go = True


@dataclass
class FakeCallback:
    data: str | None
    message: FakeMessage | None = field(default_factory=FakeMessage)
    from_user: FakeUser | None = field(default_factory=FakeUser)

    def __post_init__(self):
        self.da_answer = 0

    async def answer(self, *a, **kw):
        self.da_answer += 1


class FakeBot:
    """Ghi lại mọi tin đã gửi. `no` để giả lập Telegram trả lỗi."""

    def __init__(self):
        self.da_gui: list[tuple[int, str]] = []
        self.ban_phim: list[Any] = []
        self.no = False

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        if self.no:
            raise RuntimeError("Telegram 502")
        self.da_gui.append((chat_id, text))
        if reply_markup is not None:
            self.ban_phim.append(reply_markup)


class FakeApp:
    def __init__(self, graph):
        self.state = type("S", (), {"admin_graph": graph})()


class GraphNo:
    """Graph mà `ainvoke` luôn nổ — giả lập mất kết nối Neon."""

    def __init__(self, loi: Exception):
        self.loi = loi

    async def ainvoke(self, *a, **kw):
        raise self.loi

    async def aget_state(self, *a, **kw):
        raise self.loi




@pytest.fixture
def bot(monkeypatch) -> FakeBot:
    """Thay ADMIN.bot và hàm gửi tin bằng bản ghi nhận được."""
    fake = FakeBot()
    vai = type("Vai", (), {"name": "admin", "bot": fake})()

    async def gui(bot_, chat_id, text, reply_markup=None, parse_mode=None):
        return await bot_.send_message(chat_id, text, reply_markup=reply_markup)

    monkeypatch.setattr(webhooks, "ADMIN", vai)
    monkeypatch.setattr(webhooks, "send_message", gui)
    monkeypatch.setattr(webhooks, "is_admin", lambda uid: uid == ADMIN_ID)
    # Khoá theo chat là dict toàn cục, dọn để test không dính nhau
    webhooks._chat_locks.clear()
    return fake


def gui_tin(app, message) -> None:
    asyncio.run(webhooks.handle_admin_message(app, message))


def bam_nut(app, callback) -> None:
    asyncio.run(webhooks.handle_admin_callback(app, callback))




class TestMatKetNoiDB:
    """Đúng lỗi đã gặp: Neon ngắt kết nối, lượt chat đó chết."""

    @pytest.mark.parametrize(
        "loi",
        [
            RuntimeError("SSL connection has been closed unexpectedly"),
            RuntimeError("the connection is closed"),
            TimeoutError("Neon không trả lời"),
        ],
        ids=["SSL đứt", "connection closed", "timeout"],
    )
    def test_gui_TIN_NHAN_luc_DB_chet_thi_admin_van_nhan_duoc_hoi_am(self, bot, loi):
        app = FakeApp(GraphNo(loi))
        gui_tin(app, FakeMessage(text="/duan Bo Tai Chinh"))

        assert bot.da_gui, (
            "Bot im lặng khi DB chết — admin không phân biệt được 'hỏng' với "
            "'đang xử lý'. Phải báo một câu."
        )

    def test_gui_FILE_luc_DB_chet_thi_admin_van_nhan_duoc_hoi_am(self, bot, tmp_path):
        from TAR_agent.graph_admin.state import UploadedFile

        path = tmp_path / "tg-abc"
        path.write_text("nội dung", encoding="utf-8")
        up = UploadedFile("Tiendo.txt", path, path.stat().st_size)

        async def tai(*a, **kw):
            return up

        import app.routers.webhooks as w

        w.download_document = tai
        try:
            app_ = FakeApp(GraphNo(RuntimeError("SSL connection has been closed")))
            gui_tin(app_, FakeMessage(document=object()))
            assert bot.da_gui
        finally:
            w.download_document = import_module(
                "app.telegram.download"
            ).download_document

    def test_BAM_NUT_luc_DB_chet_thi_admin_van_nhan_duoc_hoi_am(self, bot):
        app = FakeApp(GraphNo(RuntimeError("the connection is closed")))
        cb = FakeCallback(data="ow:1")

        bam_nut(app, cb)

        assert cb.da_answer >= 1, "Không answer thì nút quay vòng vòng trên máy admin"
        assert bot.da_gui, "Bấm nút xong DB chết mà không báo gì là tệ nhất"

    def test_loi_khong_lam_chet_lan_sau(self, bot):
        """Một lượt hỏng không được để khoá chat bị kẹt."""
        app = FakeApp(GraphNo(RuntimeError("SSL closed")))
        gui_tin(app, FakeMessage(text="/duan A"))
        gui_tin(app, FakeMessage(text="/duan A"))

        assert len(bot.da_gui) == 2, "Lượt thứ hai bị khoá kẹt là bot câm vĩnh viễn"


class TestGuiTinThatBai:
    def test_telegram_no_khi_gui_thi_khong_lam_sap_handler(self, bot, graph, kho):
        """Mạng chập chờn lúc gửi. Không được để exception leo ra ngoài rồi
        thành 'Xử lý nền thất bại' mà admin không biết gì."""
        bot.no = True
        app = FakeApp(graph)

        gui_tin(app, FakeMessage(text="kho có gì thế"))  # không được ném

    def test_ban_phim_khong_gui_duoc_thi_khong_sap(self, bot, graph, kho, tmp_path):
        from TAR_agent.graph_admin.state import UploadedFile

        kho.add_project("Bo Tai Chinh")
        path = tmp_path / "tg-xyz"
        path.write_text("nội dung", encoding="utf-8")

        async def tai(*a, **kw):
            return UploadedFile("Tiendo.txt", path, path.stat().st_size)

        webhooks.download_document = tai
        try:
            bot.no = True
            gui_tin(FakeApp(graph), FakeMessage(document=object()))
        finally:
            webhooks.download_document = import_module(
                "app.telegram.download"
            ).download_document


class TestBamNutLungTung:
    """Admin bấm nhanh, bấm lại nút cũ, bấm nút của lượt đã xong."""

    def test_bam_HAI_LAN_lien_tiep_khong_lam_sap(self, bot, graph, kho, tmp_path):
        from TAR_agent.graph_admin.state import UploadedFile
        from TAR_agent.utils.text import name_uuid

        kho.add_project("Bo Tai Chinh")
        pid = name_uuid("Bo Tai Chinh")
        path = tmp_path / "tg-2lan"
        path.write_text("nội dung", encoding="utf-8")

        async def tai(*a, **kw):
            return UploadedFile("Tiendo.txt", path, path.stat().st_size)

        webhooks.download_document = tai
        try:
            app = FakeApp(graph)
            gui_tin(app, FakeMessage(document=object()))

            cb = FakeCallback(data=f"p:{pid}")
            bam_nut(app, cb)
            so_tin = len(bot.da_gui)

            # Bấm lại đúng nút đó — graph đã chạy xong, không còn interrupt
            bam_nut(app, FakeCallback(data=f"p:{pid}"))

            assert len(bot.da_gui) >= so_tin, "Không được ném exception"
        finally:
            webhooks.download_document = import_module(
                "app.telegram.download"
            ).download_document

    def test_callback_data_rac_thi_answer_roi_thoi(self, bot, graph, kho):
        cb = FakeCallback(data="rac-tu-dau-do")
        bam_nut(FakeApp(graph), cb)

        assert cb.da_answer == 1
        assert bot.da_gui == []

    def test_callback_khong_co_data(self, bot, graph, kho):
        cb = FakeCallback(data=None)
        bam_nut(FakeApp(graph), cb)
        assert cb.da_answer == 1

    def test_callback_khong_co_message(self, bot, graph, kho):
        cb = FakeCallback(data="ow:1", message=None)
        bam_nut(FakeApp(graph), cb)
        assert cb.da_answer == 1

    def test_go_ban_phim_sau_khi_bam_de_khong_bam_lai_duoc(
        self, bot, graph, kho, tmp_path
    ):
        from TAR_agent.graph_admin.state import UploadedFile
        from TAR_agent.utils.text import name_uuid

        kho.add_project("Bo Tai Chinh")
        pid = name_uuid("Bo Tai Chinh")
        path = tmp_path / "tg-goban"
        path.write_text("nội dung", encoding="utf-8")

        async def tai(*a, **kw):
            return UploadedFile("Tiendo.txt", path, path.stat().st_size)

        webhooks.download_document = tai
        try:
            app = FakeApp(graph)
            gui_tin(app, FakeMessage(document=object()))
            cb = FakeCallback(data=f"p:{pid}")
            bam_nut(app, cb)
            assert cb.message.markup_da_go
        finally:
            webhooks.download_document = import_module(
                "app.telegram.download"
            ).download_document


class TestChotQuyen:
    def test_nguoi_la_nhan_tin_thi_bot_KHONG_tra_loi_gi(self, bot, graph, kho):
        gui_tin(FakeApp(graph), FakeMessage(text="/duan Kho cua toi", from_user=FakeUser(999)))
        assert bot.da_gui == [], "Người lạ không được nhận hồi âm nào"

    def test_nguoi_la_bam_nut_thi_khong_resume_duoc_graph(self, bot, graph, kho):
        cb = FakeCallback(data="ow:1", from_user=FakeUser(999))
        bam_nut(FakeApp(graph), cb)
        assert bot.da_gui == []

    def test_tin_nhan_khong_ro_nguoi_gui(self, bot, graph, kho):
        gui_tin(FakeApp(graph), FakeMessage(text="hello", from_user=None))
        assert bot.da_gui == []


class TestTinNhanLa:
    def test_anh_sticker_khong_chu_khong_file_van_duoc_tra_loi(self, bot, graph, kho):
        """message.text và caption đều None (ảnh, sticker). Không được nổ
        AttributeError, phải rơi vào nhánh hướng dẫn."""
        gui_tin(FakeApp(graph), FakeMessage(text=None, caption=None))
        assert bot.da_gui, "Gửi ảnh cũng phải được chỉ dẫn phải làm gì"

    def test_file_kem_caption_thi_van_di_nhanh_NAP(self, bot, graph, kho, tmp_path):
        from TAR_agent.graph_admin.state import UploadedFile

        kho.add_project("Bo Tai Chinh")
        path = tmp_path / "tg-caption"
        path.write_text("nội dung", encoding="utf-8")

        async def tai(*a, **kw):
            return UploadedFile("Tiendo.txt", path, path.stat().st_size)

        webhooks.download_document = tai
        try:
            gui_tin(
                FakeApp(graph),
                FakeMessage(document=object(), caption="/duan Doi xe"),
            )
            assert webhooks.keyboard  # bàn phím chọn dự án phải hiện ra
            assert bot.ban_phim, "Có file thì phải hỏi chọn dự án, không xử caption"
        finally:
            webhooks.download_document = import_module(
                "app.telegram.download"
            ).download_document

    def test_file_bi_tu_choi_thi_bao_ly_do(self, bot, graph, kho):
        from app.telegram.download import UploadRejected

        async def tu_choi(*a, **kw):
            raise UploadRejected("too_large", file_name="To.xlsx", max_mb=20)

        webhooks.download_document = tu_choi
        try:
            gui_tin(FakeApp(graph), FakeMessage(document=object()))
            assert bot.da_gui
            assert "20" in bot.da_gui[0][1]
        finally:
            webhooks.download_document = import_module(
                "app.telegram.download"
            ).download_document
