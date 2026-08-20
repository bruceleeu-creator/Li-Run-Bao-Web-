"""协同看板 · PostgreSQL 持久层（psycopg3 连接池 + 幂等 schema）。

独立服务：不 import core/ 与 web_backend/（云端只存任务/进度数据，
财报数据不出本机的产品红线由部署边界保证）。

环境变量：
- DATABASE_URL：postgresql://board:<pwd>@db:5432/board（容器内）
- BOARD_ENV：prod 时收紧连接池参数

所有 SQL 均为内联字面量 DDL / 参数化 DML，无任何用户输入拼接。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("缺少 DATABASE_URL 环境变量（PostgreSQL 连接串）")
    return url


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = ConnectionPool(
            database_url(),
            min_size=1,
            max_size=8,
            open=True,
            timeout=30,
        )
    return _pool


@contextmanager
def conn():
    """借出自动提交连接（dict 行）；异常时回滚。"""
    pool = get_pool()
    with pool.connection() as c:
        c.row_factory = dict_row
        try:
            yield c
        except Exception:
            c.rollback()
            raise


def init_schema() -> None:
    """幂等执行全部 DDL（服务启动时调用；逐条字面量，无动态拼接）。"""
    with conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id            BIGSERIAL PRIMARY KEY,
              username      VARCHAR(32) UNIQUE NOT NULL,
              display_name  VARCHAR(64) NOT NULL,
              password_hash TEXT NOT NULL,
              color         VARCHAR(7) NOT NULL,
              created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_login_at TIMESTAMPTZ
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS rooms (
              id          BIGSERIAL PRIMARY KEY,
              name        VARCHAR(64) NOT NULL,
              owner_id    BIGINT NOT NULL REFERENCES users(id),
              invite_code VARCHAR(12) UNIQUE NOT NULL,
              version     BIGINT NOT NULL DEFAULT 0,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS room_members (
              room_id   BIGINT NOT NULL REFERENCES rooms(id),
              user_id   BIGINT NOT NULL REFERENCES users(id),
              role      VARCHAR(10) NOT NULL CHECK (role IN ('owner','member')),
              joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (room_id, user_id)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id           BIGSERIAL PRIMARY KEY,
              room_id      BIGINT NOT NULL REFERENCES rooms(id),
              title        VARCHAR(200) NOT NULL,
              detail       TEXT NOT NULL DEFAULT '',
              status       VARCHAR(10) NOT NULL DEFAULT 'todo'
                           CHECK (status IN ('todo','doing','done')),
              assignee_id  BIGINT REFERENCES users(id),
              creator_id   BIGINT NOT NULL REFERENCES users(id),
              due_date     DATE,
              priority     VARCHAR(6) NOT NULL DEFAULT 'mid'
                           CHECK (priority IN ('high','mid','low')),
              month_tag    VARCHAR(7),
              sort_no      BIGINT NOT NULL DEFAULT 0,
              created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
              completed_at TIMESTAMPTZ,
              completed_by BIGINT
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_room ON tasks(room_id, status)")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS task_events (
              id         BIGSERIAL PRIMARY KEY,
              room_id    BIGINT NOT NULL,
              task_id    BIGINT NOT NULL,
              user_id    BIGINT NOT NULL,
              action     VARCHAR(20) NOT NULL,
              payload    JSONB NOT NULL DEFAULT '{}',
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


def health() -> bool:
    try:
        with conn() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False
