"""板端 e2e 后端启动器（playwright webServer 调用）。

使用固定的 board_e2e 测试库（一次性手工创建：psql -c "CREATE DATABASE board_e2e"）。
启动时：幂等建表 + 字面量 TRUNCATE 清态（e2e 单 worker 串行，轮次间隔离足够），
随后在 127.0.0.1:8090 起看板服务。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

COLLAB_ROOT = Path(__file__).resolve().parent
if str(COLLAB_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLAB_ROOT))

db_url = os.environ.get("BOARD_E2E_DB", "").strip() or "postgresql://board@127.0.0.1:54329/board_e2e"
os.environ["DATABASE_URL"] = db_url
os.environ.setdefault("JWT_SECRET", "e2e-secret-0123456789abcdef0123456789abcdef")

db = importlib.import_module("board_backend.CO_db_WB-CO-TR-20260820")
db.init_schema()
with db.conn() as c:
    c.execute("TRUNCATE task_events, tasks, room_members, rooms, users RESTART IDENTITY CASCADE")

app_module = importlib.import_module("board_backend.CO_app_WB-CO-TR-20260820")
app = app_module.create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8090)
