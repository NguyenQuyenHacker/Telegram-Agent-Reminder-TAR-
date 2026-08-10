"""Điểm chạy local trên Windows.

psycopg async không chạy được trên ProactorEventLoop (mặc định của Windows),
nên phải đổi policy TRƯỚC khi uvicorn tạo event loop. Chạy `uvicorn app.main:app`
trực tiếp sẽ lỗi InterfaceError, vì vậy dev local dùng: python run.py

Trên Linux (Docker/Railway) không cần file này, cứ dùng uvicorn như CMD trong Dockerfile.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
