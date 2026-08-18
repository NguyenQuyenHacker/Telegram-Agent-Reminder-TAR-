"""Hàm thuần của subgraph SQL: kiểm câu lệnh và dọn kết quả.

Thân các node nằm ở `SqlGraph`. Ở đây chỉ có thứ không cần model, không cần DB,
và vì thế test được mà không dựng gì cả.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Câu lệnh phải mở đầu bằng SELECT hoặc WITH. Không phải danh sách đen (đen thì
# luôn thiếu một từ khoá nào đó) mà là danh sách TRẮNG hai từ.
_STARTS_OK = re.compile(r"^\s*(?:SELECT|WITH)\b", re.IGNORECASE)

# Tên đủ điều kiện bị cấm. `readonly_tx` đã đặt `search_path = data` nên model
# viết `FROM cong_viec` là đủ; viết `data.cong_viec` thì đúng nhưng thừa, còn
# `public.` / `pg_` / `information_schema` thì `tar_ro` không có quyền và câu
# lệnh chỉ tổ nổ permission denied ở `execute` sau khi đã tốn một lượt LLM.
_QUALIFIED = re.compile(
    r"\b(?:data\s*\.|public\s*\.|pg_[a-z_]+|information_schema)", re.IGNORECASE
)

# Comment SQL: `--` tới hết dòng, và `/* ... */`. Gỡ trước khi kiểm, không thì
# một dấu `;` nấp trong comment làm câu hợp lệ bị từ chối.
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

_HAS_LIMIT = re.compile(r"\blimit\b", re.IGNORECASE)

# Model từ chối sinh SQL vì bảng không có trường câu hỏi cần. Xem mục "Khi câu
# hỏi đòi thứ bảng không có" trong prompts/client_system/gen_sql.md.
_UNSUPPORTED = re.compile(r"^\s*KHONG_TRA_LOI_DUOC\s*:?\s*(.*)", re.IGNORECASE | re.DOTALL)

# Dùng khi model phát đúng token từ chối nhưng bỏ trống phần lý do. Thà một câu
# chung chung còn hơn để `compose` nhận chuỗi rỗng rồi tự nghĩ ra lý do.
DEFAULT_UNSUPPORTED = (
    "bảng lịch công việc không có trường dữ liệu mà câu hỏi này cần"
)


def unsupported_reason(reply: str) -> str | None:
    """Lý do từ chối, hoặc None nếu đây là một câu SQL bình thường.

    Tách khỏi `validate` CHỦ Ý: `validate` trả lời "câu này có chạy được không",
    còn hàm này trả lời "model có chịu viết câu nào không". Gộp hai câu hỏi đó
    vào một hàm thì lời từ chối đi vào nhánh `repair` — mà `repair` sẽ ngoan
    ngoãn nặn ra một câu SQL, tức là đúng cái hành vi bịa số ta vừa chặn.

    Nhận cả dạng thiếu dấu hai chấm: model bỏ sót một ký tự dấu câu không phải
    lý do để cả cơ chế từ chối im lặng biến mất.
    """
    match = _UNSUPPORTED.match(reply or "")
    if not match:
        return None
    # Gộp mọi khoảng trắng: lý do đi thẳng vào câu trả lời cho người dùng, mà
    # model hay xuống dòng giữa chừng.
    return " ".join(match.group(1).split()) or DEFAULT_UNSUPPORTED


def _strip(sql: str) -> str:
    """Bỏ comment và dấu `;` cuối câu. Dùng cho phần KIỂM, không phải phần chạy."""
    return _COMMENT.sub(" ", sql).strip().rstrip(";").strip()


def validate(sql: str, row_limit: int) -> tuple[str | None, str]:
    """`(lý do trượt hoặc None, câu SQL đã chuẩn hoá)`.

    Thay cho `query_checker` bằng LLM của pattern SQL agent thông thường: bắt
    được gần hết rác mà không tốn một lượt LLM, và không bao giờ nghĩ ra hai
    kết luận khác nhau cho cùng một câu — thứ mà một model chấm SQL luôn có thể.

    Bốn luật:
      - đúng MỘT câu, mở đầu bằng SELECT hoặc WITH
      - không có `;` ở giữa
      - không tên đủ điều kiện (`data.`, `public.`, `pg_`, `information_schema`)
      - ép LIMIT nếu thiếu

    Không bắt được SQL sai về NGỮ NGHĨA (đúng cú pháp, sai ý) — nhưng cái đó
    `query_checker` cũng không bắt được đáng tin, còn `execute` thì bắt được mọi
    lỗi cú pháp thật và đưa nguyên văn cho `repair`.

    Lý do trả về đi thẳng vào prompt của `repair` nên nó viết cho model đọc.
    """
    cleaned = _strip(sql or "")
    if not cleaned:
        return "Câu lệnh rỗng.", ""
    if not _STARTS_OK.match(cleaned):
        return "Câu lệnh phải bắt đầu bằng SELECT hoặc WITH.", cleaned
    if ";" in cleaned:
        return "Chỉ được viết ĐÚNG MỘT câu lệnh, không có dấu ; ở giữa.", cleaned
    if match := _QUALIFIED.search(cleaned):
        return (
            f"Không được viết tên đủ điều kiện «{match.group().strip()}». "
            f"Chỉ viết `FROM cong_viec`, không có tiền tố schema.",
            cleaned,
        )
    if not _HAS_LIMIT.search(cleaned):
        cleaned = f"{cleaned} LIMIT {row_limit}"
    return None, cleaned


def to_jsonable(value: Any) -> Any:
    """Giá trị Postgres -> thứ `json.dumps` nuốt được.

    Node `retrieve` serialize payload thành JSON để nhét vào `messages`, nên một
    `date` lọt qua đây là cả lượt hỏi chết ở tầng ngoài — xa chỗ gây ra, sau khi
    đã tốn hết tiền của lượt đó.

    `Decimal` -> `float`: `SUM()` trên cột INTEGER vẫn ra Decimal.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
