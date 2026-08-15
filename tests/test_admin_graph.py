"""Luồng admin đầu-cuối, chạy qua graph thật với kho giả.

Vì sao có file này: bộ kiểm cũ (`check_graph.py`) dùng MỘT thread_id RIÊNG cho
mỗi kịch bản, nên nó không bao giờ thấy state của lượt trước. Ngoài đời thì
ngược lại — cả một đoạn chat Telegram dùng CHUNG một thread_id, lượt này nối
tiếp lượt kia. Đó là chỗ lọt con bug "báo `no_projects` dù dự án đã tồn tại".

Nên nhóm test đầu tiên ở đây cố tình chạy NHIỀU LƯỢT trên cùng một thread_id.

Không đụng Postgres, không gọi API embedding — xem FakeKho trong conftest.
"""

import pytest
from conftest import ADMIN_ID, kinds, resume, turn, waiting_for

SHA_KHAC = "sha-cua-ban-cu-khac-han"


def cfg(name: str = "chat") -> dict:
    """Một đoạn chat Telegram = một thread_id, dùng cho MỌI lượt trong test."""
    return {"configurable": {"thread_id": f"admin:{name}"}}


class TestStateRacGiuaCacLuot:
    """Nhóm quan trọng nhất: state của lượt trước KHÔNG được rớt sang lượt sau.

    Các field như `error`, `outcome` không có reducer nên LangGraph GIỮ NGUYÊN
    giá trị cũ khi node không trả về khoá đó. `route` phải dọn sạch mỗi lượt.
    """

    def test_bao_no_projects_roi_tao_du_an_thi_luot_sau_PHAI_chay(
        self, graph, kho, upload_factory
    ):
        """Đúng con bug đã gặp thật trên Telegram.

        Gửi file khi kho rỗng -> no_projects. Tạo dự án. Gửi lại file -> phải
        hỏi chọn dự án, KHÔNG được báo lại no_projects.
        """
        c = cfg("bug-no-projects")

        out = turn(graph, c, upload=upload_factory("Tiendo.txt"))
        assert kinds(out) == ["ingest_failed"]
        assert out["outbox"][0]["data"]["reason"] == "no_projects"

        turn(graph, c, text="/duan Bo Tai Chinh")

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        cho = waiting_for(graph, c)
        assert cho is not None, "Phải dừng hỏi chọn dự án, không được báo lỗi cũ"
        assert cho["kind"] == "choose_project"

    def test_nap_xong_roi_nhan_chu_thi_KHONG_bao_lai_ket_qua_nap(
        self, graph, kho, upload_factory
    ):
        """Lượt nạp xong để lại outcome='created'. Lượt sau gõ chữ mà không dọn
        thì report cũ có thể sống lại thành 'ingest_done' lần hai."""
        c = cfg("nap-roi-chat")
        pid = kho.add_project("Bo Tai Chinh")

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        out = resume(graph, c, {"project_id": str(pid)})
        assert kinds(out) == ["ingest_done"]

        out = turn(graph, c, text="kho có gì thế")
        assert kinds(out) == ["hint"]

    def test_huy_ghi_de_roi_nap_file_KHAC_thi_khong_bi_huy_lay(
        self, graph, kho, upload_factory
    ):
        """outcome='cancelled' của lượt trước không được kéo lượt sau đi thẳng
        tới report."""
        c = cfg("huy-roi-nap")
        pid = kho.add_project("Bo Tai Chinh")
        kho.add_document(pid, "Tiendo.txt", SHA_KHAC, chunk_count=42)

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        resume(graph, c, {"project_id": str(pid)})
        out = resume(graph, c, {"overwrite": False})
        assert kinds(out) == ["ingest_cancelled"]

        turn(graph, c, upload=upload_factory("BaoCaoMoi.txt"))
        out = resume(graph, c, {"project_id": str(pid)})
        assert kinds(out) == ["ingest_done"], "File mới phải được nạp bình thường"

    def test_luot_unchanged_khong_de_lai_so_dem_cho_luot_sau(
        self, graph, kho, upload_factory
    ):
        """existing_chunk_count=42 của lượt 'unchanged' rớt sang lượt sau là
        báo sai số đoạn cho một file hoàn toàn khác."""
        c = cfg("unchanged-roi-nap")
        pid = kho.add_project("Bo Tai Chinh")
        noi_dung = "Nội dung y hệt."

        up = upload_factory("Tiendo.txt", noi_dung)
        turn(graph, c, upload=up)
        out = resume(graph, c, {"project_id": str(pid)})
        assert kinds(out) == ["ingest_done"]
        so_doan = out["outbox"][0]["data"]["chunk_count"]

        # Gửi lại y hệt -> unchanged, báo đúng số đoạn của bản đang có
        turn(graph, c, upload=upload_factory("Tiendo.txt", noi_dung))
        out = resume(graph, c, {"project_id": str(pid)})
        assert kinds(out) == ["ingest_unchanged"]
        assert out["outbox"][0]["data"]["chunk_count"] == so_doan

        # File khác hẳn -> số đoạn phải của file mới, không phải số cũ
        turn(graph, c, upload=upload_factory("KhacHan.txt", "Một nội dung khác."))
        out = resume(graph, c, {"project_id": str(pid)})
        assert kinds(out) == ["ingest_done"]
        assert out["outbox"][0]["data"]["replaced_chunk_count"] == 0


class TestNapLanDau:
    def test_dung_hoi_chon_du_an_va_liet_ke_du_du_an(self, graph, kho, upload_factory):
        c = cfg()
        kho.add_project("Bo Tai Chinh")
        kho.add_project("Doi xe")

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        cho = waiting_for(graph, c)

        assert cho["kind"] == "choose_project"
        assert cho["data"]["file_name"] == "Tiendo.txt"
        assert {p["name"] for p in cho["data"]["projects"]} == {
            "Bo Tai Chinh",
            "Doi xe",
        }

    def test_chon_xong_thi_ghi_va_bao_du_thong_tin(self, graph, kho, upload_factory):
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outcome"] == "created"
        data = out["outbox"][0]["data"]
        assert data["file_name"] == "Tiendo.txt"
        assert data["project_name"] == "Bo Tai Chinh"
        assert data["chunk_count"] > 0
        assert data["as_of_date"], "Luôn phải in mốc dữ liệu để admin soát"

        assert len(kho.save_calls) == 1
        assert kho.save_calls[0].file_kind == "txt"
        assert kho.save_calls[0].uploaded_by == ADMIN_ID

    def test_kho_chua_co_du_an_thi_tu_choi_va_KHONG_hoi_gi(
        self, graph, kho, upload_factory
    ):
        c = cfg()
        out = turn(graph, c, upload=upload_factory("Tiendo.txt"))

        assert out["outbox"][0]["data"]["reason"] == "no_projects"
        assert waiting_for(graph, c) is None, "Không được hiện bàn phím rỗng"
        assert kho.embed_calls == 0

    def test_du_an_vua_chon_bi_xoa_trong_luc_admin_dang_nhin_ban_phim(
        self, graph, kho, upload_factory
    ):
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        kho.add_project("Doi xe")
        turn(graph, c, upload=upload_factory("Tiendo.txt"))

        del kho.projects[pid]  # người khác vừa xoá đúng dự án admin sắp bấm
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outbox"][0]["data"]["reason"] == "project_gone"
        assert kho.embed_calls == 0

    def test_xoa_SACH_du_an_trong_luc_dang_nhin_ban_phim_thi_ra_no_projects(
        self, graph, kho, upload_factory
    ):
        """Node chứa interrupt chạy LẠI TỪ DÒNG 1 khi resume, nên nó đọc lại
        danh sách dự án. Kho rỗng thì rơi vào nhánh no_projects trước khi kịp
        xét lựa chọn — vẫn đúng, chỉ khác mã lỗi."""
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        turn(graph, c, upload=upload_factory("Tiendo.txt"))

        kho.projects.clear()
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outbox"][0]["data"]["reason"] == "no_projects"
        assert kho.embed_calls == 0


class TestNapLai:
    def test_noi_dung_Y_HET_thi_bo_qua_khong_hoi_khong_embed(
        self, graph, kho, upload_factory
    ):
        """Nhánh tiết kiệm nhất: hash tính từ bytes thô nên chưa cần parse."""
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        noi_dung = "Y hệt bản cũ."

        turn(graph, c, upload=upload_factory("Tiendo.txt", noi_dung))
        resume(graph, c, {"project_id": str(pid)})
        embed_sau_lan_dau = kho.embed_calls

        turn(graph, c, upload=upload_factory("Tiendo.txt", noi_dung))
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outcome"] == "unchanged"
        assert waiting_for(graph, c) is None, "KHÔNG được hỏi ghi đè"
        assert kho.embed_calls == embed_sau_lan_dau, "Không được embed lần nữa"
        assert len(kho.save_calls) == 1

    def test_noi_dung_KHAC_thi_hoi_ghi_de(self, graph, kho, upload_factory):
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        kho.add_document(pid, "Tiendo.txt", SHA_KHAC, chunk_count=42)

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        resume(graph, c, {"project_id": str(pid)})
        cho = waiting_for(graph, c)

        assert cho["kind"] == "confirm_overwrite"
        assert cho["data"]["file_name"] == "Tiendo.txt"
        assert cho["data"]["existing_chunk_count"] == 42

    def test_bam_HUY_thi_khong_ton_mot_dong_nao(self, graph, kho, upload_factory):
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        kho.add_document(pid, "Tiendo.txt", SHA_KHAC, chunk_count=42)

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        resume(graph, c, {"project_id": str(pid)})
        out = resume(graph, c, {"overwrite": False})

        assert out["outcome"] == "cancelled"
        assert kho.embed_calls == 0, "Huỷ mà vẫn embed là đốt tiền vô ích"
        assert kho.save_calls == []
        assert kho.documents[(pid, "Tiendo.txt")].chunk_count == 42

    def test_bam_GHI_DE_thi_thay_ban_cu_va_bao_so_doan_da_thay(
        self, graph, kho, upload_factory
    ):
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        kho.add_document(pid, "Tiendo.txt", SHA_KHAC, chunk_count=42)

        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        resume(graph, c, {"project_id": str(pid)})
        out = resume(graph, c, {"overwrite": True})

        assert out["outcome"] == "updated"
        assert kinds(out) == ["ingest_updated"]
        assert out["outbox"][0]["data"]["replaced_chunk_count"] == 42
        assert len(kho.save_calls) == 1

    def test_hau_to_copy_cua_telegram_duoc_coi_la_CUNG_tai_lieu(
        self, graph, kho, upload_factory
    ):
        """`Tiendo(1).txt` là bản sửa của `Tiendo.txt`, không phải file thứ hai.
        Tên lưu trong kho phải giữ nguyên bản gốc."""
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        kho.add_document(pid, "Tiendo.txt", SHA_KHAC, chunk_count=42)

        turn(graph, c, upload=upload_factory("Tiendo(1).txt"))
        resume(graph, c, {"project_id": str(pid)})
        out = resume(graph, c, {"overwrite": True})

        assert out["outcome"] == "updated"
        assert kho.save_calls[0].file_name == "Tiendo.txt"

    def test_hai_file_khac_ten_cung_song(self, graph, kho, upload_factory):
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")

        turn(graph, c, upload=upload_factory("TiendoT6.txt", "Số liệu tháng 6."))
        resume(graph, c, {"project_id": str(pid)})
        turn(graph, c, upload=upload_factory("TiendoT7.txt", "Số liệu tháng 7."))
        resume(graph, c, {"project_id": str(pid)})

        assert {name for _, name in kho.documents} == {"TiendoT6.txt", "TiendoT7.txt"}


class TestDuongLoi:
    def test_file_tam_bi_xoa_truoc_khi_admin_bam(self, graph, kho, upload_factory):
        """App restart giữa lúc admin chưa bấm -> file tạm bay mất. Phải báo
        tử tế, không ném traceback."""
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        up = upload_factory("Tiendo.txt")

        turn(graph, c, upload=up)
        up.path.unlink()
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outbox"][0]["data"]["reason"] == "temp_file_gone"
        assert kho.embed_calls == 0

    def test_file_rong_thi_bao_empty_document(self, graph, kho, upload_factory):
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")

        turn(graph, c, upload=upload_factory("Rong.txt", "   \n\n  "))
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outbox"][0]["data"]["reason"] == "empty_document"
        assert kho.embed_calls == 0

    def test_loader_no_thi_bao_parse_failed_khong_ném_traceback(
        self, graph, kho, upload_factory, monkeypatch
    ):
        from TAR_agent.graph_admin.helpers import loaders

        def no(*a, **kw):
            raise RuntimeError("file hỏng")

        monkeypatch.setattr(loaders, "load", no)

        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        turn(graph, c, upload=upload_factory("Hong.txt"))
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outbox"][0]["data"]["reason"] == "parse_failed"

    def test_embedding_hong_thi_KHONG_ghi_gi_vao_kho(
        self, graph, kho, upload_factory, monkeypatch
    ):
        from importlib import import_module

        async def no(*a, **kw):
            raise RuntimeError("Gemini 503")

        # import_module chứ không `from graph_admin.nodes import store`:
        # nodes/__init__.py xuất lại hàm trùng tên module (xem conftest.kho)
        monkeypatch.setattr(
            import_module("TAR_agent.graph_admin.nodes.store"), "embed_documents", no
        )

        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outbox"][0]["data"]["reason"] == "embed_failed"
        assert kho.save_calls == [], "Embed hỏng mà vẫn ghi là kho có tài liệu 0 vector"

    def test_ghi_DB_hong_thi_bao_write_failed(
        self, graph, kho, upload_factory, monkeypatch
    ):
        from TAR_agent.graph_admin.helpers import writer

        def no(**kw):
            raise RuntimeError("mất kết nối Neon")

        monkeypatch.setattr(writer, "save", no)

        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        turn(graph, c, upload=upload_factory("Tiendo.txt"))
        out = resume(graph, c, {"project_id": str(pid)})

        assert out["outbox"][0]["data"]["reason"] == "write_failed"

    @pytest.mark.parametrize(
        "ket_thuc",
        ["thanh_cong", "huy", "loi"],
        ids=["nạp xong", "admin huỷ", "gặp lỗi"],
    )
    def test_moi_duong_ket_thuc_deu_xoa_file_tam(
        self, graph, kho, upload_factory, ket_thuc
    ):
        """`report` là cửa ra duy nhất và là chỗ dọn file tạm. Sót một đường là
        mỗi lượt nạp để lại rác trên đĩa."""
        c = cfg()
        pid = kho.add_project("Bo Tai Chinh")
        up = upload_factory("Tiendo.txt")

        if ket_thuc == "loi":
            kho.projects.clear()
            turn(graph, c, upload=up)
        else:
            if ket_thuc == "huy":
                kho.add_document(pid, "Tiendo.txt", SHA_KHAC)
            turn(graph, c, upload=up)
            resume(graph, c, {"project_id": str(pid)})
            if ket_thuc == "huy":
                resume(graph, c, {"overwrite": False})

        assert not up.path.exists(), f"Còn sót file tạm ở nhánh {ket_thuc}"


class TestNhanhTinNhanChu:
    def test_duan_kem_ten_thi_tao(self, graph, kho):
        out = turn(graph, cfg(), text="/duan Bo Tai Chinh")
        assert kinds(out) == ["project_created"]
        assert out["outbox"][0]["data"]["name"] == "Bo Tai Chinh"

    def test_duan_trung_ten_thi_bao_da_co_chu_khong_tao_hai_lan(self, graph, kho):
        c = cfg()
        turn(graph, c, text="/duan Doi xe")
        out = turn(graph, c, text="/duan  ĐỘI XE  ")

        assert kinds(out) == ["project_exists"]
        assert len(kho.projects) == 1, "Chuẩn hoá tên phải gộp về một dự án"

    def test_duan_khong_ten_thi_liet_ke(self, graph, kho):
        kho.add_project("Bo Tai Chinh", document_count=3)
        out = turn(graph, cfg(), text="/duan")

        assert kinds(out) == ["project_list"]
        assert out["outbox"][0]["data"]["projects"] == [
            {"name": "Bo Tai Chinh", "document_count": 3}
        ]

    def test_duan_khi_kho_rong(self, graph, kho):
        out = turn(graph, cfg(), text="/duan")
        assert out["outbox"][0]["data"]["projects"] == []

    def test_ten_toan_emoji_thi_bao_ten_khong_hop_le(self, graph, kho):
        out = turn(graph, cfg(), text="/duan 🎯🎯")
        assert kinds(out) == ["project_invalid_name"]
        assert kho.projects == {}

    def test_chu_thuong_thi_ra_huong_dan(self, graph, kho):
        out = turn(graph, cfg(), text="kho có gì thế")
        assert kinds(out) == ["hint"]


class TestBayCuaTangTelegram:
    """Ghi lại bằng test cái bẫy mà app/routers/webhooks.py phải né."""

    def test_dang_dung_cho_bam_thi_outbox_van_la_cua_luot_TRUOC(
        self, graph, kho, upload_factory
    ):
        """`outbox` không có reducer nên chỉ bị GHI ĐÈ khi `report` chạy. Lúc
        graph đang dừng ở interrupt thì report CHƯA chạy, nên out["outbox"] vẫn
        là của lượt trước.

        Vì vậy `webhooks._reply` BẮT BUỘC kiểm interrupt TRƯỚC khi đọc outbox —
        đảo thứ tự là gửi lại tin cũ cho một câu hỏi mới.
        """
        c = cfg()
        kho.add_project("Bo Tai Chinh")

        out = turn(graph, c, text="/duan Bo Tai Chinh")
        assert kinds(out) == ["project_exists"]

        out = turn(graph, c, upload=upload_factory("Tiendo.txt"))

        assert waiting_for(graph, c)["kind"] == "choose_project"
        assert kinds(out) == ["project_exists"], (
            "Đây chính là tin CŨ còn sót. Tầng Telegram phải hỏi interrupt trước."
        )
