"""协同看板 · 认证：注册 / 登录 / JWT / 登录限流 / 当前用户。

安全基线：bcrypt cost 12 加盐哈希；JWT HS256（7 天，secret=env JWT_SECRET）；
统一错误文案「用户名或密码错误」；内存令牌桶每 IP 10 次/分钟，超限 429。
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from psycopg.rows import dict_row

import importlib

db = importlib.import_module("board_backend.CO_db_WB-CO-TR-20260820")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 8 色板（纸墨系）：注册时轮询分配，个人可改
COLOR_PALETTE = ("#3D5A80", "#8C5A3C", "#3F6B4F", "#6B4A6E",
                 "#8F8A3D", "#6E5238", "#38566E", "#2F4B6B")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

_login_buckets: dict[str, list[float]] = {}
_LOGIN_WINDOW = 60.0
_LOGIN_MAX = 10


def _secret() -> str:
    s = os.environ.get("JWT_SECRET", "").strip()
    if not s:
        raise RuntimeError("缺少 JWT_SECRET 环境变量（须 ≥32 位随机）")
    return s


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except Exception:
        return False


def make_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def _check_login_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = [t for t in _login_buckets.get(ip, []) if now - t < _LOGIN_WINDOW]
    if len(bucket) >= _LOGIN_MAX:
        _login_buckets[ip] = bucket
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请一分钟后再试")
    bucket.append(now)
    _login_buckets[ip] = bucket


class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class MePatch(BaseModel):
    display_name: str | None = None
    color: str | None = None


def _public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "color": row["color"],
    }


@router.post("/register")
def register(body: RegisterIn, request: Request) -> dict:
    if not _USERNAME_RE.match(body.username or ""):
        raise HTTPException(status_code=400, detail="用户名须为 3~32 位字母/数字/下划线/连字符")
    if len(body.password or "") < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    _check_login_rate(request)
    with db.conn() as c:
        exists = c.execute(
            "SELECT 1 FROM users WHERE username = %s", (body.username,)
        ).fetchone()
        if exists is not None:
            raise HTTPException(status_code=400, detail="用户名已被注册")
        # 8 色板轮询分配：按当前用户数取模，尽量错开
        count = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        color = COLOR_PALETTE[int(count) % len(COLOR_PALETTE)]
        row = c.execute(
            """
            INSERT INTO users (username, display_name, password_hash, color)
            VALUES (%s, %s, %s, %s)
            RETURNING id, username, display_name, color
            """,
            (body.username, (body.display_name or body.username)[:64],
             _hash_password(body.password), color),
        ).fetchone()
        return {"token": make_token(row["id"], row["username"]), "user": _public_user(row)}


@router.post("/login")
def login(body: LoginIn, request: Request) -> dict:
    _check_login_rate(request)
    with db.conn() as c:
        row = c.execute(
            "SELECT id, username, display_name, color, password_hash FROM users WHERE username = %s",
            (body.username or "",),
        ).fetchone()
    if row is None or not _verify_password(body.password or "", row["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    with db.conn() as c:
        c.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (row["id"],))
    return {"token": make_token(row["id"], row["username"]), "user": _public_user(row)}


_bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """解析 Bearer JWT → 当前用户（id/username/display_name/color）。"""
    if creds is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(creds.credentials, _secret(), algorithms=["HS256"])
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    with db.conn() as c:
        row = c.execute(
            "SELECT id, username, display_name, color FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="账户不存在")
    return _public_user(row)


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@router.patch("/me")
def patch_me(body: MePatch, user: dict = Depends(current_user)) -> dict:
    if body.color is not None:
        if not re.match(r"^#[0-9A-Fa-f]{6}$", body.color):
            raise HTTPException(status_code=400, detail="颜色须为 #RRGGBB 格式")
    with db.conn() as c:
        if body.display_name:
            c.execute(
                "UPDATE users SET display_name = %s WHERE id = %s",
                (body.display_name[:64], user["id"]),
            )
        if body.color is not None:
            c.execute("UPDATE users SET color = %s WHERE id = %s", (body.color, user["id"]))
        row = c.execute(
            "SELECT id, username, display_name, color FROM users WHERE id = %s", (user["id"],)
        ).fetchone()
    return _public_user(row)
