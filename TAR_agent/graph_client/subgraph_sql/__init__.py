"""Vòng sinh-kiểm-chạy SQL. Nhánh thứ hai của `tools/retrieval.py`.

Cùng khuôn với `graph_client/subgraph/` (vòng tra tài liệu tự sửa truy vấn):
graph client bên ngoài không biết cụm này tồn tại, nó chỉ thấy `rows` trong
payload hoặc không.

    state.py   SqlState — question · project_id · sql · rows · columns · error
    nodes.py   `validate` (Python thuần) và `to_jsonable`. Không LLM, không DB.
    graph.py   class SqlGraph, compile một lần thành SQL_GRAPH.
"""
