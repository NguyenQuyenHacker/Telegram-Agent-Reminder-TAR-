"""Câu hỏi -> các chunk gần nghĩa nhất, trong phạm vi MỘT dự án.

Chưa biết đang hỏi dự án nào thì hỏi lại, đừng tra toàn kho: trả lời câu hỏi của
dự án này bằng tài liệu của dự án khác, mà câu trả lời đó trông vẫn rất thật.

TODO khi cài:
    project = resolve_project(...)        # shared.projects
    chunks = await retriever.search(project.project_id, question, k)
    return {"retrieved": chunks}
"""

from TAR_agent.graph_client.state import ClientState


async def retrieve(state: ClientState) -> dict:
    raise NotImplementedError
