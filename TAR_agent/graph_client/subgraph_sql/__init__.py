"""Vòng sinh-kiểm-chạy SQL, nằm TRONG tool `query_data`.

Cùng khuôn với `graph_client/subgraph/` (vòng tra cứu tự sửa truy vấn): graph
client bên ngoài không biết cụm này tồn tại, nó chỉ thấy một tool nữa trong
`CLIENT_TOOLS`.

    state.py   SqlState — question · project_id · sql · rows · columns · error
    nodes.py   `validate` (Python thuần) và `to_jsonable`. Không LLM, không DB.
    graph.py   class SqlGraph, compile một lần thành SQL_GRAPH.
"""
