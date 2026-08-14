"""Tool của agent client — CHỈ ĐỌC, không ngoại lệ.

Danh sách này tách hẳn khỏi ADMIN_TOOLS và không import từ graph_admin. Gộp một
danh sách rồi lọc lúc chạy là sớm muộn cũng lọt.
"""

from TAR_agent.graph_client.tools.search import list_projects, search_documents

CLIENT_TOOLS = [list_projects, search_documents]

__all__ = ["CLIENT_TOOLS", "list_projects", "search_documents"]
