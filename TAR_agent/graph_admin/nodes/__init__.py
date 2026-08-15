"""Tầng node: đọc state -> gọi utils -> trả về phần state cần cập nhật.

Node phải MỎNG. Nghiệp vụ thật nằm ở TAR_agent/graph_admin/helpers — hàm thuần, test được
không cần dựng graph. Node dày lên là dấu hiệu có thứ đáng nằm ở utils.

Node trả về dict cập nhật state, KHÔNG sửa state tại chỗ và KHÔNG gửi tin nhắn.

`ask_project` và `check_file` chứa `interrupt()` nên phải sạch tuyệt đối: chúng
chạy lại từ dòng 1 khi resume.
"""

from TAR_agent.graph_admin.nodes.ask_project import ask_project
from TAR_agent.graph_admin.nodes.check_file import check_file
from TAR_agent.graph_admin.nodes.extract import Extract
from TAR_agent.graph_admin.nodes.handle_text import handle_text
from TAR_agent.graph_admin.nodes.parse import parse
from TAR_agent.graph_admin.nodes.report import report
from TAR_agent.graph_admin.nodes.route import choose_branch, route
from TAR_agent.graph_admin.nodes.store import store

__all__ = [
    "Extract",
    "ask_project",
    "check_file",
    "choose_branch",
    "handle_text",
    "parse",
    "report",
    "route",
    "store",
]
