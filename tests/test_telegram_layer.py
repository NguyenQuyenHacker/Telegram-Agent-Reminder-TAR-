"""Tầng Telegram: render, bàn phím, nhận file.

Ba thứ này là nơi lỗi KHÔNG làm graph hỏng nhưng làm admin nhận sai tin, hoặc
không nhận được gì. Graph chạy đúng mà tin nhắn ra sai thì cũng như hỏng.

Không dựng bot thật: render và keyboard là hàm thuần, còn download chỉ cần một
`message.document` giả.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.telegram import keyboard
from app.telegram.download import UploadRejected, download_document, sweep_stale_uploads
from app.telegram.render import render


class TestRender:
    """Mọi event lõi sinh ra đều phải render được thành câu tiếng Việt."""

    @pytest.mark.parametrize(
        "event",
        [
            {
                "kind": "ingest_done",
                "data": {
                    "file_name": "TiendoT6.xlsx",
                    "project_name": "Bo Tai Chinh",
                    "chunk_count": 16,
                    "replaced_chunk_count": 0,
                    "as_of_date": "2026-06-30",
                },
            },
            {
                "kind": "ingest_updated",
                "data": {
                    "file_name": "TiendoT6.xlsx",
                    "project_name": "Bo Tai Chinh",
                    "chunk_count": 20,
                    "replaced_chunk_count": 16,
                    "as_of_date": "2026-06-30",
                },
            },
            {
                "kind": "ingest_unchanged",
                "data": {
                    "file_name": "TiendoT6.xlsx",
                    "project_name": "Bo Tai Chinh",
                    "chunk_count": 16,
                    "replaced_chunk_count": 0,
                    "as_of_date": None,
                },
            },
            {
                "kind": "ingest_cancelled",
                "data": {
                    "file_name": "TiendoT6.xlsx",
                    "project_name": "Bo Tai Chinh",
                    "chunk_count": 16,
                    "replaced_chunk_count": 0,
                    "as_of_date": None,
                },
            },
            {"kind": "ingest_failed", "data": {"reason": "no_projects"}},
            {"kind": "ingest_failed", "data": {"reason": "temp_file_gone"}},
            {"kind": "ingest_failed", "data": {"reason": "empty_document"}},
            {"kind": "ingest_failed", "data": {"reason": "parse_failed"}},
            {"kind": "ingest_failed", "data": {"reason": "embed_failed"}},
            {"kind": "ingest_failed", "data": {"reason": "write_failed"}},
            {"kind": "ingest_failed", "data": {"reason": "project_gone"}},
            {"kind": "hint", "data": {}},
            {"kind": "project_created", "data": {"name": "Bo Tai Chinh"}},
            {"kind": "project_exists", "data": {"name": "Bo Tai Chinh"}},
            {"kind": "project_invalid_name", "data": {"name": "🎯"}},
            {"kind": "project_list", "data": {"projects": []}},
            {
                "kind": "project_list",
                "data": {"projects": [{"name": "Doi xe", "document_count": 3}]},
            },
        ],
        ids=lambda e: f"{e['kind']}:{e['data'].get('reason', '')}",
    )
    def test_moi_event_deu_ra_chuoi_khong_rong(self, event):
        text = render(event)
        assert isinstance(text, str) and text.strip()

    def test_kind_la_KHONG_duoc_nem_KeyError(self):
        """Một event không render được không đáng làm chết cả lượt trả lời."""
        assert render({"kind": "kind_chua_ai_viet", "data": {}}).strip()

    def test_thieu_khoa_trong_data_cung_khong_duoc_no(self):
        """Lõi đổi hình dạng data mà quên sửa render thì admin vẫn nhận được
        tin, chứ không phải im lặng."""
        assert render({"kind": "ingest_done", "data": {}}).strip()

    def test_ma_loi_la_van_bao_duoc_cho_admin_biet(self):
        text = render({"kind": "ingest_failed", "data": {"reason": "loi_moi_toanh"}})
        assert "loi_moi_toanh" in text, "Phải lộ mã lỗi để còn tra được"

    def test_ingest_updated_neu_ro_so_doan_da_thay(self):
        text = render(
            {
                "kind": "ingest_updated",
                "data": {
                    "file_name": "T6.xlsx",
                    "project_name": "BTC",
                    "chunk_count": 20,
                    "replaced_chunk_count": 16,
                    "as_of_date": "2026-06-30",
                },
            }
        )
        assert "20" in text and "16" in text

    def test_ten_file_co_ky_tu_HTML_van_di_qua_nguyen_ven(self):
        """sender.py gửi text thuần (parse_mode=None). Nếu ai đó bật HTML mà
        quên escape thì Telegram trả 400 và tin nhắn MẤT LUÔN."""
        text = render(
            {
                "kind": "ingest_done",
                "data": {
                    "file_name": "<b>bao&cao</b>.xlsx",
                    "project_name": "A<B",
                    "chunk_count": 1,
                    "replaced_chunk_count": 0,
                    "as_of_date": None,
                },
            }
        )
        assert "<b>bao&cao</b>.xlsx" in text


class TestKeyboard:
    def test_moi_du_an_mot_nut_va_bam_ra_dung_id(self):
        projects = [
            {"project_id": "11111111-1111-5111-8111-111111111111", "name": "A", "document_count": 2},
            {"project_id": "22222222-2222-5222-8222-222222222222", "name": "B", "document_count": 0},
        ]
        markup = keyboard.choose_project_keyboard(projects)
        assert len(markup.inline_keyboard) == 2

        nut = markup.inline_keyboard[0][0]
        assert keyboard.resume_value(nut.callback_data) == {
            "project_id": "11111111-1111-5111-8111-111111111111"
        }

    def test_callback_data_KHONG_qua_64_byte(self):
        """Telegram từ chối callback_data > 64 byte. Vượt là bàn phím không gửi
        được, admin ngồi chờ mãi không thấy gì."""
        projects = [
            {
                "project_id": "11111111-1111-5111-8111-111111111111",
                "name": "Tên dự án rất dài " * 5,
                "document_count": 99,
            }
        ]
        markup = keyboard.choose_project_keyboard(projects)
        data = markup.inline_keyboard[0][0].callback_data
        assert len(data.encode("utf-8")) <= 64

    def test_nut_ghi_de_va_huy_ra_dung_gia_tri(self):
        markup = keyboard.confirm_overwrite_keyboard()
        ghi_de, huy = markup.inline_keyboard[0]

        assert keyboard.resume_value(ghi_de.callback_data) == {"overwrite": True}
        assert keyboard.resume_value(huy.callback_data) == {"overwrite": False}

    def test_callback_data_la_thi_nem_ValueError(self):
        """Admin bấm nút của một lượt nạp cũ. Phải phân biệt được với lỗi thật."""
        with pytest.raises(ValueError):
            keyboard.resume_value("rac-tu-dau-do")

    def test_ten_du_an_hien_kem_so_tai_lieu(self):
        markup = keyboard.choose_project_keyboard(
            [{"project_id": "x", "name": "Doi xe", "document_count": 7}]
        )
        assert "Doi xe" in markup.inline_keyboard[0][0].text
        assert "7" in markup.inline_keyboard[0][0].text


# ─────────────────────── download.py ───────────────────────


@dataclass
class FakeDoc:
    file_name: str | None
    file_size: int
    file_unique_id: str = "AgAD123"


@dataclass
class FakeMessage:
    document: FakeDoc | None


class FakeBot:
    """Bot giả: `download` chép nội dung ra file đích như aiogram làm."""

    def __init__(self, content: bytes = b"noi dung file"):
        self.content = content
        self.no = False

    async def download(self, document, destination):
        if self.no:
            raise RuntimeError("mất mạng")
        Path(destination).write_bytes(self.content)


class TestDownload:
    def test_duoi_file_la_thi_tu_choi_TRUOC_khi_tai(self):
        bot = FakeBot()
        message = FakeMessage(FakeDoc("bao-cao.docx", 1000))

        with pytest.raises(UploadRejected) as loi:
            asyncio.run(download_document(bot, message))
        assert loi.value.reason == "unsupported_format"

    def test_file_qua_nang_thi_tu_choi_TRUOC_khi_tai(self):
        bot = FakeBot()
        message = FakeMessage(FakeDoc("Tiendo.xlsx", 999 * 1024 * 1024))

        with pytest.raises(UploadRejected) as loi:
            asyncio.run(download_document(bot, message))
        assert loi.value.reason == "too_large"

    def test_tai_hong_thi_bao_download_failed(self):
        bot = FakeBot()
        bot.no = True
        message = FakeMessage(FakeDoc("Tiendo.txt", 100))

        with pytest.raises(UploadRejected) as loi:
            asyncio.run(download_document(bot, message))
        assert loi.value.reason == "download_failed"

    def test_khong_co_document_thi_bao_no_document(self):
        with pytest.raises(UploadRejected) as loi:
            asyncio.run(download_document(FakeBot(), FakeMessage(None)))
        assert loi.value.reason == "no_document"

    @pytest.mark.parametrize("ten", ["Tiendo.txt", "Tiendo.TXT", "Tiendo.xlsx"])
    def test_duoi_hop_le_thi_nhan_ke_ca_viet_HOA(self, ten):
        up = asyncio.run(download_document(FakeBot(), FakeMessage(FakeDoc(ten, 100))))
        try:
            assert up.file_name == ten
            assert up.path.exists()
            assert up.size_bytes == len(b"noi dung file")
        finally:
            up.path.unlink(missing_ok=True)

    def test_file_tam_KHONG_mang_duoi_that(self):
        """Giữ đúng điều kiện thật: aiogram không thêm đuôi. Loader nào ngầm
        dựa vào đuôi đường dẫn phải lộ ra ở test, không phải lúc admin gửi file.
        """
        up = asyncio.run(
            download_document(FakeBot(), FakeMessage(FakeDoc("Tiendo.xlsx", 100)))
        )
        try:
            assert up.path.suffix == ""
        finally:
            up.path.unlink(missing_ok=True)

    def test_document_khong_co_ten_thi_tu_choi_chu_khong_no(self):
        """file_name của Telegram có thể None. Không được ném AttributeError."""
        with pytest.raises(UploadRejected) as loi:
            asyncio.run(download_document(FakeBot(), FakeMessage(FakeDoc(None, 100))))
        assert loi.value.reason == "unsupported_format"

    def test_quet_file_tam_khong_no_khi_thu_muc_chua_ton_tai(self):
        assert isinstance(sweep_stale_uploads(), int)

    def test_quet_giu_file_moi_va_xoa_file_cu(self, monkeypatch, tmp_path: Path):
        import time

        from importlib import import_module

        dl = import_module("app.telegram.download")
        monkeypatch.setattr(dl, "_UPLOAD_DIR", tmp_path)

        moi = tmp_path / "vua-tai"
        moi.write_bytes(b"x")
        cu = tmp_path / "tu-hom-qua"
        cu.write_bytes(b"x")
        qua_khu = time.time() - 48 * 3600
        import os

        os.utime(cu, (qua_khu, qua_khu))

        assert dl.sweep_stale_uploads(max_age_hours=24) == 1
        assert moi.exists(), "File của lượt nạp đang chờ admin bấm KHÔNG được xoá"
        assert not cu.exists()
