"""协同看板 · 房间：创建 / 我的房间 / 凭码加入（锁定）/ 重置码 / 成员管理。

权限隔离：路径参数 rid 一律经 require_member 依赖校验 membership，
非本房间成员访问任何房间数据返回 403。
邀请码 8 位 Crockford base32；加入失败 4 次/小时锁定（按 用户+房间）。
"""
from __future__ import annotations

import importlib
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

db = importlib.import_module("board_backend.CO_db_WB-CO-TR-20260820")
auth = importlib.import_module("board_backend.CO_auth_WB-CO-TR-20260820")

router = APIRouter(prefix="/api/rooms", tags=["rooms"])

# Crockford base32（去 I L O U，防手抄混淆）
_INVITE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_JOIN_WINDOW = 3600.0
_JOIN_MAX_FAILS = 4
_join_fails: dict[tuple[int, int], list[float]] = {}


def new_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))


class RoomIn(BaseModel):
    name: str


class JoinIn(BaseModel):
    invite_code: str


def _get_room(c, room_id: int) -> dict:
    row = c.execute("SELECT id, name, owner_id, invite_code, version FROM rooms WHERE id = %s",
                    (room_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="房间不存在")
    return row


def _my_role(c, room_id: int, user_id: int) -> str | None:
    row = c.execute(
        "SELECT role FROM room_members WHERE room_id = %s AND user_id = %s",
        (room_id, user_id),
    ).fetchone()
    return row["role"] if row else None


def require_member(room_id: int, user: dict = Depends(auth.current_user)) -> dict:
    """房间级权限隔离：非成员 403。返回附 room/role 的上下文。"""
    with db.conn() as c:
        room = _get_room(c, room_id)
        role = _my_role(c, room_id, user["id"])
    if role is None:
        raise HTTPException(status_code=403, detail="你不是该房间成员，无法访问")
    return {"user": user, "room": room, "role": role}


def require_owner(ctx: dict = None) -> dict:
    if ctx["role"] != "owner":
        raise HTTPException(status_code=403, detail="仅房间负责人可执行该操作")
    return ctx


@router.post("")
def create_room(body: RoomIn, user: dict = Depends(auth.current_user)) -> dict:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="房间名不能为空")
    with db.conn() as c:
        room = c.execute(
            """
            INSERT INTO rooms (name, owner_id, invite_code) VALUES (%s, %s, %s)
            RETURNING id, name, owner_id, invite_code, version
            """,
            (name[:64], user["id"], new_invite_code()),
        ).fetchone()
        c.execute(
            "INSERT INTO room_members (room_id, user_id, role) VALUES (%s, %s, 'owner')",
            (room["id"], user["id"]),
        )
    return {"room": room, "role": "owner"}


@router.get("")
def my_rooms(user: dict = Depends(auth.current_user)) -> dict:
    with db.conn() as c:
        rows = c.execute(
            """
            SELECT r.id, r.name, r.version, m.role,
                   (SELECT COUNT(*) FROM tasks t WHERE t.room_id = r.id) AS total,
                   (SELECT COUNT(*) FROM tasks t WHERE t.room_id = r.id AND t.status = 'done') AS done,
                   (SELECT COUNT(*) FROM room_members mm WHERE mm.room_id = r.id) AS member_count
            FROM rooms r JOIN room_members m ON m.room_id = r.id
            WHERE m.user_id = %s
            ORDER BY r.id DESC
            """,
            (user["id"],),
        ).fetchall()
    rooms = []
    for r in rows:
        total = int(r["total"] or 0)
        done = int(r["done"] or 0)
        rooms.append({
            "id": r["id"], "name": r["name"], "role": r["role"],
            "version": int(r["version"] or 0),
            "member_count": int(r["member_count"] or 0),
            "stats": {"total": total, "done": done,
                      "completion": round(done / total, 4) if total else 0.0},
        })
    return {"rooms": rooms}


@router.post("/join-by-code")
def join_by_code(body: JoinIn, user: dict = Depends(auth.current_user)) -> dict:
    """凭邀请码加入房间（前端只有码不知房间 id 的入口）。

    锁定与错误语义同 /{room_id}/join：按 用户+码 计算 4 次/小时。
    """
    code = (body.invite_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="请输入邀请码")
    key = ("code", user["id"], code)
    now = time.time()
    fails = [t for t in _join_fails.get(key, []) if now - t < _JOIN_WINDOW]
    if len(fails) >= _JOIN_MAX_FAILS:
        _join_fails[key] = fails
        raise HTTPException(status_code=429, detail="邀请码错误次数过多，请一小时后再试")
    with db.conn() as c:
        room = c.execute(
            "SELECT id, name, invite_code FROM rooms WHERE invite_code = %s", (code,)
        ).fetchone()
        if room is None:
            fails.append(now)
            _join_fails[key] = fails
            raise HTTPException(status_code=400, detail="邀请码无效或已被重置")
        role = _my_role(c, room["id"], user["id"])
        if role is not None:
            return {"room": {"id": room["id"], "name": room["name"]}, "role": role, "already": True}
        c.execute(
            "INSERT INTO room_members (room_id, user_id, role) VALUES (%s, %s, 'member')",
            (room["id"], user["id"]),
        )
        c.execute("UPDATE rooms SET version = version + 1 WHERE id = %s", (room["id"],))
    _join_fails.pop(key, None)
    return {"room": {"id": room["id"], "name": room["name"]}, "role": "member", "already": False}


@router.post("/{room_id}/join")
def join_room(room_id: int, body: JoinIn, user: dict = Depends(auth.current_user)) -> dict:
    key = (user["id"], room_id)
    now = time.time()
    fails = [t for t in _join_fails.get(key, []) if now - t < _JOIN_WINDOW]
    if len(fails) >= _JOIN_MAX_FAILS:
        _join_fails[key] = fails
        raise HTTPException(status_code=429, detail="邀请码错误次数过多，请一小时后再试")
    with db.conn() as c:
        room = _get_room(c, room_id)
        role = _my_role(c, room_id, user["id"])
        if role is not None:
            return {"room": {"id": room["id"], "name": room["name"]}, "role": role,
                    "already": True}
        code = (body.invite_code or "").strip().upper()
        if code != room["invite_code"]:
            fails.append(now)
            _join_fails[key] = fails
            remaining = _JOIN_MAX_FAILS - len(fails)
            raise HTTPException(status_code=400,
                                detail=f"邀请码错误（剩余尝试次数：{max(0, remaining)}）")
        c.execute(
            "INSERT INTO room_members (room_id, user_id, role) VALUES (%s, %s, 'member')",
            (room_id, user["id"]),
        )
        c.execute("UPDATE rooms SET version = version + 1 WHERE id = %s", (room_id,))
    _join_fails.pop(key, None)
    return {"room": {"id": room["id"], "name": room["name"]}, "role": "member", "already": False}


@router.post("/{room_id}/invite/refresh")
def refresh_invite(ctx: dict = Depends(require_member)) -> dict:
    require_owner(ctx)
    room_id = ctx["room"]["id"]
    with db.conn() as c:
        code = new_invite_code()
        c.execute("UPDATE rooms SET invite_code = %s WHERE id = %s", (code, room_id))
    return {"invite_code": code}


@router.get("/{room_id}/members")
def list_members(ctx: dict = Depends(require_member)) -> dict:
    room_id = ctx["room"]["id"]
    with db.conn() as c:
        rows = c.execute(
            """
            SELECT u.id, u.username, u.display_name, u.color, m.role, m.joined_at
            FROM room_members m JOIN users u ON u.id = m.user_id
            WHERE m.room_id = %s ORDER BY m.joined_at
            """,
            (room_id,),
        ).fetchall()
    return {"members": [
        {"id": r["id"], "username": r["username"], "display_name": r["display_name"],
         "color": r["color"], "role": r["role"],
         "joined_at": r["joined_at"].isoformat() if r["joined_at"] else None}
        for r in rows
    ]}


@router.delete("/{room_id}/members/{user_id}")
def remove_member(room_id: int, user_id: int, ctx: dict = Depends(require_member)) -> dict:
    require_owner(ctx)
    if user_id == ctx["user"]["id"]:
        raise HTTPException(status_code=400, detail="不能移除自己（房间负责人）")
    with db.conn() as c:
        role = _my_role(c, room_id, user_id)
        if role is None:
            raise HTTPException(status_code=404, detail="该用户不是房间成员")
        if role == "owner":
            raise HTTPException(status_code=400, detail="不能移除房间负责人")
        c.execute("DELETE FROM room_members WHERE room_id = %s AND user_id = %s",
                  (room_id, user_id))
        # 历史任务保留（creator 色条按 users.color 快照显示，users 行不删）
        c.execute("UPDATE rooms SET version = version + 1 WHERE id = %s", (room_id,))
    return {"removed": user_id}
