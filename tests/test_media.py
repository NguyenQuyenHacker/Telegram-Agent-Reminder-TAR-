"""Phần thuần của đầu vào ảnh / tin nhắn thoại.

Đọc một update Telegram xem nó mang gì (extract_media) và soạn message gửi cho
model (_human_message) đều không cần mạng. Việc tải file và gọi model thì cần,
nên không nằm trong bộ này.
"""

from datetime import datetime

import pytest
from aiogram.types import Audio, Chat, Document, Message, PhotoSize, Voice

from app.telegram.media import extract_media
from app.telegram.messages import media_failed_text, media_understood_text
from reminder_agent.utils.media_text import _human_message

_EMPTY_FIELDS = dict(
    message_id=1,
    chat=Chat(id=1, type="private"),
    caption=None,
    text=None,
    photo=None,
    voice=None,
    audio=None,
    document=None,
)


@pytest.fixture
def make_message():
    def _make(**fields) -> Message:
        # model_construct: dựng thẳng object, không chạy validator của aiogram —
        # ở đây chỉ cần đúng vài trường extract_media đọc tới.
        return Message.model_construct(
            **{**_EMPTY_FIELDS, "date": datetime.now(), **fields}
        )

    return _make


class TestExtractMedia:
    def test_anh_lay_ban_net_nhat(self, make_message):
        # photo là nhiều cỡ của CÙNG một ảnh; lấy nhầm bản nhỏ là chữ trong ảnh
        # chụp màn hình không còn đọc được
        sizes = [
            PhotoSize(file_id="nho", file_unique_id="a", width=90, height=90),
            PhotoSize(file_id="to", file_unique_id="b", width=1280, height=960),
        ]
        media = extract_media(make_message(photo=sizes))
        assert (media.kind, media.file_id) == ("photo", "to")

    def test_giu_lai_chu_thich_kem_anh(self, make_message):
        sizes = [PhotoSize(file_id="x", file_unique_id="a", width=9, height=9)]
        media = extract_media(make_message(photo=sizes, caption="cái này hạn thứ 6"))
        assert media.caption == "cái này hạn thứ 6"

    def test_tin_nhan_thoai(self, make_message):
        voice = Voice(
            file_id="v1", file_unique_id="c", duration=7, mime_type="audio/ogg"
        )
        media = extract_media(make_message(voice=voice))
        assert (media.kind, media.mime_type) == ("voice", "audio/ogg")

    def test_thieu_mime_thi_lay_mac_dinh(self, make_message):
        voice = Voice(file_id="v1", file_unique_id="c", duration=7)
        assert extract_media(make_message(voice=voice)).mime_type == "audio/ogg"

    def test_file_am_thanh_cung_duoc_nhan(self, make_message):
        audio = Audio(
            file_id="a1", file_unique_id="d", duration=30, mime_type="audio/mpeg"
        )
        assert extract_media(make_message(audio=audio)).kind == "voice"

    def test_anh_gui_dang_file_van_duoc_nhan(self, make_message):
        # Nút "gửi không nén" của Telegram cho ra document, nội dung thì y hệt
        document = Document(file_id="d1", file_unique_id="e", mime_type="image/png")
        media = extract_media(make_message(document=document))
        assert (media.kind, media.mime_type) == ("photo", "image/png")

    def test_file_khac_thi_bo_qua(self, make_message):
        document = Document(
            file_id="d2", file_unique_id="f", mime_type="application/pdf"
        )
        assert extract_media(make_message(document=document)) is None

    def test_tin_nhan_khong_co_gi_thi_bo_qua(self, make_message):
        assert extract_media(make_message()) is None


class TestHumanMessage:
    def _parts(self, **kwargs) -> list[dict]:
        return _human_message(**kwargs).content

    def test_binh_thuong_gom_phan_dan_va_phan_nhi_phan(self):
        parts = self._parts(
            kind="voice", data=b"\x00\x01", mime_type="audio/ogg", caption=""
        )
        assert parts[0]["type"] == "text"
        # Part kiểu "media" mới mang được audio; "image_url" thì không
        assert parts[1] == {"type": "media", "mime_type": "audio/ogg", "data": b"\x00\x01"}

    def test_chu_thich_di_vao_phan_dan(self):
        parts = self._parts(
            kind="photo", data=b"\x00", mime_type="image/jpeg", caption="hạn thứ 6"
        )
        assert "hạn thứ 6" in parts[0]["text"]

    def test_khong_co_chu_thich_thi_khong_de_lai_dong_rong(self):
        parts = self._parts(
            kind="photo", data=b"\x00", mime_type="image/jpeg", caption=""
        )
        assert "Chú thích" not in parts[0]["text"]


class TestMediaMessages:
    def test_tra_lai_nguyen_van_thu_doc_duoc(self):
        # Người dùng phải soát được thứ bot nghe/đọc TRƯỚC khi nó hành động
        assert "Nộp báo cáo" in media_understood_text("voice", "Nộp báo cáo")

    def test_moi_loai_co_cau_bao_hong_rieng(self):
        assert "ảnh" in media_failed_text("photo")
        assert "thoại" in media_failed_text("voice")

    def test_loai_la_van_co_cau_tra_loi(self):
        assert media_failed_text("video") != ""
        assert media_understood_text("video", "abc").endswith("abc")
