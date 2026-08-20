"""协同看板测试夹具：独立 PG 库 + 每测试清表 + TestClient。

运行前提：本机/容器可达的 PostgreSQL，通过环境变量指定：
  BOARD_TEST_DATABASE_URL=postgresql://board:board@127.0.0.1:54329/board_test
未设置或不可达时全部跳过（本地无 PG 不阻塞主项目门禁）。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

COLLAB_ROOT = Path(__file__).resolve().parents[2]  # collab_board/
if str(COLLAB_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLAB_ROOT))

TEST_DB_URL = os.environ.get("BOARD_TEST_DATABASE_URL", "").strip()

db = importlib.import_module("board_backend.CO_db_WB-CO-TR-20260820")


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
    try:
        db.init_schema()
    except Exception as e:  # PG 不可达 → 整体跳过
        pytest.skip(f"测试数据库不可达：{e}")
    yield
    # 显式关闭连接池，避免进程退出时等待后台线程（Windows 下尤为明显）
    try:
        if db._pool is not None and not db._pool.closed:
            db._pool.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    # lock_timeout 快速失败：若库被残留连接持锁，测试应报错而非无限等待
    # （本地测试曾被强杀进程留下的 idle-in-transaction 连接拖死）
    with db.conn() as c:
        c.execute("SET LOCAL lock_timeout = '5s'")
        try:
            c.execute("TRUNCATE task_events, tasks, room_members, rooms, users RESTART IDENTITY CASCADE")
        except Exception:
            c.rollback()
            rows = c.execute(
                """
                SELECT pid, state, wait_event_type, wait_event,
                       pg_blocking_pids(pid) AS blocked_by, left(query, 60) AS q
                FROM pg_stat_activity WHERE datname = current_database()
                """
            ).fetchall()
            print("\n[LOCK DEBUG] activity:", rows, flush=True)
            raise


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    app_mod = importlib.import_module("board_backend.CO_app_WB-CO-TR-20260820")
    auth = importlib.import_module("board_backend.CO_auth_WB-CO-TR-20260820")
    auth._login_buckets.clear()
    rooms_mod = importlib.import_module("board_backend.CO_rooms_WB-CO-TR-20260820")
    rooms_mod._join_fails.clear()
    with TestClient(app_mod.create_app()) as c:
        yield c


def register(client, username: str, password: str = "password123") -> dict:
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make_room(client, token: str, name: str = "测试房间") -> dict:
    r = client.post("/api/rooms", json={"name": name}, headers=auth_headers(token))
    assert r.status_code == 200, r.text
    return r.json()["room"]
