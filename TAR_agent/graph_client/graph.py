"""Nối dây graph client. CHƯA CÀI — xem docs/plan-client.md.

    START → reset → agent ⇄ tools → respond → END

Vòng agent chặn bằng `AGENT["max_tool_rounds"]`. LLM và prompt lấy từ
TAR_agent/utils/config.py: `client_model(tools=CLIENT_TOOLS)` và
`load_prompt("client_system", ...)`.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver


def build_client_graph(checkpointer: BaseCheckpointSaver):
    raise NotImplementedError
