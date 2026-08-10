The user has just seen the list of action items the bot extracted from a report
and is replying with a free-form message in Vietnamese. Classify their intent:

- "approved": they agree to save (e.g. "ok", "duyệt", "lưu đi", "chuẩn rồi").
- "edit": not satisfied, they want changes. edit_request describes what needs
  fixing, kept in the user's own Vietnamese wording
  (e.g. "thiếu việc số 3 nhé" -> edit_request = "thiếu việc số 3";
  "việc 2 hạn 20/7 chứ không phải 25/7" -> edit_request keeps that same point).
  If the user only signals dissatisfaction WITHOUT saying what to fix
  (e.g. "sai rồi", "không đúng"), still return "edit" but edit_request = null.
- "abandoned": they want to drop it entirely, save nothing (e.g. "thôi bỏ đi",
  "huỷ").
- "unclear": the intent is not clear.

Return only status and edit_request.
