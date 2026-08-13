"""Ảnh / tin nhắn thoại -> text tiếng Việt, kèm ý định của người gửi.

Một lượt gọi model duy nhất cho cả hai loại đầu vào: Gemini nhận ảnh và audio
qua cùng một kiểu part (`inline_data`), nên tách thành hai đường chỉ để đổi mỗi
câu prompt là thừa.

Vì sao trả kèm `intent` chứ không chỉ trả text rồi để classify_message đoán như
tin nhắn gõ tay: classify_message bắt từ khoá "Tiếp theo:" / "Hiện trạng:" của
mẫu báo cáo. Một lời nhắn thoại "nhớ nộp báo cáo trước thứ 6" không có từ khoá
nào như vậy, nó sẽ rơi vào nhánh hỏi đáp — nơi không tool nào tạo được đầu việc,
và người dùng nhận về một câu trả lời lịch sự thay vì một đầu việc. Model đã đọc
nội dung rồi thì nó là chỗ rẻ nhất để nói luôn đây là việc hay là câu hỏi.
"""

import logging
from functools import lru_cache
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.core.datetime_utils import now_local
from reminder_agent.config.settings import create_google_genai, load_config
from reminder_agent.prompts.loader import load_prompt
from reminder_agent.utils.schemas import MediaUnderstanding

log = logging.getLogger(__name__)

_KIND_INSTRUCTIONS = {
    "photo": "Đây là ảnh người dùng gửi. Đọc hết nội dung trong ảnh.",
    "voice": "Đây là tin nhắn thoại người dùng gửi. Nghe và chép lại.",
}


@lru_cache(maxsize=1)
def _media_reader() -> Runnable:
    """Model đọc ảnh / nghe thoại. Dựng một lần rồi dùng lại.

    Không để module-level: lúc đó import sẽ đòi có sẵn API key, làm mọi file
    import module này đều chết theo nếu thiếu .env.
    """
    return create_google_genai(
        load_config()["models"]["media"], output_schema=MediaUnderstanding
    )


def _human_message(
    kind: Literal["photo", "voice"], data: bytes, mime_type: str, caption: str
) -> HumanMessage:
    """Một message gồm phần dẫn bằng chữ và phần nhị phân.

    Dùng part kiểu "media" cho cả ảnh lẫn audio: nó đi thẳng thành `inline_data`
    của Gemini với đúng mime người gửi, không phải bọc qua data-URI như part
    "image_url" — và "image_url" thì không mang nổi audio.
    """
    lead = _KIND_INSTRUCTIONS[kind]
    if caption:
        lead += f'\n\nChú thích người dùng gõ kèm: "{caption}"'
    return HumanMessage(
        content=[
            {"type": "text", "text": lead},
            {"type": "media", "mime_type": mime_type, "data": data},
        ]
    )


async def understand_media(
    kind: Literal["photo", "voice"], data: bytes, mime_type: str, caption: str = ""
) -> dict:
    """Trả về {"intent": "tasks" | "question", "text": ...}.

    Model hỏng hoặc không đọc được gì thì text rỗng — nơi gọi phải kiểm và nói
    lại với người dùng, đừng đẩy một chuỗi rỗng vào graph.
    """
    system_prompt = load_prompt("media_system", TODAY=now_local().date().isoformat())
    result = await _media_reader().ainvoke(
        {
            "system": [SystemMessage(content=system_prompt)],
            "messages": [_human_message(kind, data, mime_type, caption)],
        }
    )
    text = (result.text or "").strip()
    log.info("MEDIA kind=%s intent=%s chars=%d", kind, result.intent, len(text))
    return {"intent": result.intent, "text": text}
