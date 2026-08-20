"""协同看板 · 任务：CRUD / 看板聚合（轮询）/ 统计 / 批量创建。

不变式：任务写操作在单事务内完成「改 tasks + rooms.version+=1 + 插 task_event」。
GET /board?version=n 相同版本返回 {unchanged:true}（省流量）。
逾期口径：due_date < 今日 且 status != 'done'。
"""
from __future__ import annotations

import importlib
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

db = importlib.import_module("board_backend.CO_db_WB-CO-TR-20260820")
rooms = importlib.import_module("board_backend.CO_rooms_WB-CO-TR-20260820")

router = APIRouter(prefix="/api/rooms/{room_id}", tags=["tasks"])

BATCH_LIMIT = 200


class TaskIn(BaseModel):
    title: str
    detail: str = ""
    assignee_id: Optional[int] = None
    due_date: Optional[str] = None
    priority: str = "mid"
    month_tag: Optional[str] = None


class TaskPatch(BaseModel):
    title: Optional[str] = None
    detail: Optional[str] = None
    status: Optional[str] = None
    assignee_id: Optional[int] = None
    due_date: Optional[str] = None
    priority: Optional[str] = None
    month_tag: Optional[str] = None


class BatchIn(BaseModel):
    items: list[TaskIn]


def _norm_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _task_row(r: dict, members_by_id: dict) -> dict:
    creator = members_by_id.get(r["creator_id"]) or {}
    assignee = members_by_id.get(r["assignee_id"]) if r["assignee_id"] else None
    return {
        "id": r["id"],
        "title": r["title"],
        "detail": r["detail"],
        "status": r["status"],
        "priority": r["priority"],
        "due_date": r["due_date"].isoformat() if r["due_date"] else None,
        "month_tag": r["month_tag"],
        "creator_id": r["creator_id"],
        "creator_name": creator.get("display_name") or creator.get("username") or "",
        "creator_color": creator.get("color") or "#999999",
        "assignee_id": r["assignee_id"],
        "assignee_name": (assignee or {}).get("display_name") or (assignee or {}).get("username") or "",
        "assignee_color": (assignee or {}).get("color"),
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
        "completed_by": r["completed_by"],
    }


def _members_map(c, room_id: int) -> dict:
    rows = c.execute(
        """
        SELECT u.id, u.username, u.display_name, u.color
        FROM room_members m JOIN users u ON u.id = m.user_id
        WHERE m.room_id = %s
        """,
        (room_id,),
    ).fetchall()
    return {r["id"]: r for r in rows}


def _users_map(c, room_id: int) -> dict:
    """任务展示用的用户映射：现任成员 ∪ 历史创建人/负责人。

    成员被移出房间后，其历史任务仍按 users 表快照显示姓名与颜色。
    """
    rows = c.execute(
        """
        SELECT DISTINCT u.id, u.username, u.display_name, u.color
        FROM users u
        WHERE u.id IN (
          SELECT user_id FROM room_members WHERE room_id = %s
          UNION
          SELECT creator_id FROM tasks WHERE room_id = %s
          UNION
          SELECT assignee_id FROM tasks WHERE room_id = %s AND assignee_id IS NOT NULL
        )
        """,
        (room_id, room_id, room_id),
    ).fetchall()
    return {r["id"]: r for r in rows}


def _stats(rows: list[dict]) -> dict:
    today = date.today()
    total = len(rows)
    done = sum(1 for r in rows if r["status"] == "done")
    doing = sum(1 for r in rows if r["status"] == "doing")
    overdue = sum(
        1 for r in rows
        if r["status"] != "done" and r["due_date"] and r["due_date"] < today
    )
    due_today = sum(
        1 for r in rows
        if r["status"] != "done" and r["due_date"] == today
    )
    return {
        "total": total, "done": done, "doing": doing,
        "overdue": overdue, "due_today": due_today,
        "completion": round(done / total, 4) if total else 0.0,
    }


def _room_tasks(c, room_id: int) -> list[dict]:
    return c.execute(
        "SELECT * FROM tasks WHERE room_id = %s ORDER BY sort_no, id", (room_id,)
    ).fetchall()


@router.get("/board")
def board(version: int = -1, ctx: dict = Depends(rooms.require_member)) -> dict:
    room = ctx["room"]
    if version >= 0 and int(room["version"]) == version:
        return {"unchanged": True, "version": version}
    with db.conn() as c:
        tasks = _room_tasks(c, room["id"])
        members = c.execute(
            """
            SELECT u.id, u.username, u.display_name, u.color, m.role
            FROM room_members m JOIN users u ON u.id = m.user_id
            WHERE m.room_id = %s ORDER BY m.joined_at
            """,
            (room["id"],),
        ).fetchall()
        # 任务展示映射（含历史创建人）必须与查询同事务：连接出 with 块后已归还
        # 连接池，绝不复用（曾致 idle-in-transaction 持锁阻塞 TRUNCATE）
        members_by_id = _users_map(c, room["id"])
    return {
        "unchanged": False,
        "version": int(room["version"]),
        "tasks": [_task_row(t, members_by_id) for t in tasks],
        "members": [
            {"id": m["id"], "username": m["username"], "display_name": m["display_name"],
             "color": m["color"], "role": m["role"]}
            for m in members
        ],
        "stats": _stats(tasks),
    }


@router.post("/tasks")
def create_task(body: TaskIn, ctx: dict = Depends(rooms.require_member)) -> dict:
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="任务标题不能为空")
    if body.priority not in ("high", "mid", "low"):
        raise HTTPException(status_code=400, detail="优先级须为 high/mid/low")
    room_id = ctx["room"]["id"]
    user_id = ctx["user"]["id"]
    with db.conn() as c:
        members = _members_map(c, room_id)
        if body.assignee_id is not None and body.assignee_id not in members:
            raise HTTPException(status_code=400, detail="负责人必须是房间成员")
        row = c.execute(
            """
            INSERT INTO tasks (room_id, title, detail, status, assignee_id, creator_id,
                               due_date, priority, month_tag)
            VALUES (%s, %s, %s, 'todo', %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (room_id, title[:200], body.detail or "", body.assignee_id, user_id,
             _norm_date(body.due_date), body.priority, _norm_month(body.month_tag)),
        ).fetchone()
        c.execute("UPDATE rooms SET version = version + 1 WHERE id = %s", (room_id,))
        c.execute(
            "INSERT INTO task_events (room_id, task_id, user_id, action, payload) VALUES (%s, %s, %s, 'create', %s)",
            (room_id, row["id"], user_id, _json_payload({"title": title[:200]})),
        )
    return {"task": _task_row(row, members)}


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, ctx: dict = Depends(rooms.require_member)) -> dict:
    room_id = ctx["room"]["id"]
    user_id = ctx["user"]["id"]
    if body.status is not None and body.status not in ("todo", "doing", "done"):
        raise HTTPException(status_code=400, detail="状态须为 todo/doing/done")
    if body.priority is not None and body.priority not in ("high", "mid", "low"):
        raise HTTPException(status_code=400, detail="优先级须为 high/mid/low")
    sets = []
    params: list = []
    provided = body.model_fields_set  # 只处理显式提供的字段，未提供一律不动
    for field, value in (
        ("title", (body.title or "").strip()[:200] if body.title is not None else None),
        ("detail", body.detail),
        ("status", body.status),
        ("assignee_id", body.assignee_id),
        ("due_date", _norm_date(body.due_date) if body.due_date is not None else None),
        ("priority", body.priority),
        ("month_tag", _norm_month(body.month_tag) if body.month_tag is not None else None),
    ):
        if field not in provided:
            continue
        if field == "title" and not value:
            continue  # 空标题不落库（保持原值）
        sets.append(f"{field} = %s")
        params.append(value)
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM tasks WHERE id = %s AND room_id = %s", (task_id, room_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        members = _members_map(c, room_id)
        if body.assignee_id is not None and body.assignee_id not in members:
            raise HTTPException(status_code=400, detail="负责人必须是房间成员")
        # status→done 记 completed_at/by；从 done 改回则清空
        if body.status == "done" and row["status"] != "done":
            sets.append("completed_at = now()")
            sets.append("completed_by = %s")
            params.append(user_id)
        elif body.status in ("todo", "doing") and row["status"] == "done":
            sets.append("completed_at = NULL")
            sets.append("completed_by = NULL")
        sets.append("updated_at = now()")
        params.extend([task_id, room_id])
        updated = c.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s AND room_id = %s RETURNING *",
            params,
        ).fetchone()
        c.execute("UPDATE rooms SET version = version + 1 WHERE id = %s", (room_id,))
        c.execute(
            "INSERT INTO task_events (room_id, task_id, user_id, action, payload) VALUES (%s, %s, %s, 'update', %s)",
            (room_id, task_id, user_id,
             _json_payload({"fields": sorted(
                 k for k in ("title", "detail", "status", "assignee_id",
                             "due_date", "priority", "month_tag")
                 if k in provided)})),
        )
    return {"task": _task_row(updated, members)}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, ctx: dict = Depends(rooms.require_member)) -> dict:
    room_id = ctx["room"]["id"]
    user_id = ctx["user"]["id"]
    with db.conn() as c:
        row = c.execute(
            "SELECT id FROM tasks WHERE id = %s AND room_id = %s", (task_id, room_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        c.execute("DELETE FROM tasks WHERE id = %s AND room_id = %s", (task_id, room_id))
        c.execute("UPDATE rooms SET version = version + 1 WHERE id = %s", (room_id,))
        c.execute(
            "INSERT INTO task_events (room_id, task_id, user_id, action, payload) VALUES (%s, %s, %s, 'delete', %s)",
            (room_id, task_id, user_id, _json_payload({})),
        )
    return {"deleted": task_id}


@router.post("/tasks/batch")
def batch_create(body: BatchIn, ctx: dict = Depends(rooms.require_member)) -> dict:
    items = body.items or []
    if not items:
        raise HTTPException(status_code=400, detail="批量清单为空")
    if len(items) > BATCH_LIMIT:
        raise HTTPException(status_code=400, detail=f"单批最多 {BATCH_LIMIT} 条")
    room_id = ctx["room"]["id"]
    user_id = ctx["user"]["id"]
    created = 0
    with db.conn() as c:
        member_ids = set(_members_map(c, room_id).keys())
        for it in items:
            title = (it.title or "").strip()
            if not title:
                continue
            assignee = it.assignee_id if it.assignee_id in member_ids else None
            row = c.execute(
                """
                INSERT INTO tasks (room_id, title, detail, status, assignee_id, creator_id,
                                   due_date, priority, month_tag)
                VALUES (%s, %s, %s, 'todo', %s, %s, %s, %s, %s) RETURNING id
                """,
                (room_id, title[:200], it.detail or "", assignee, user_id,
                 _norm_date(it.due_date), it.priority if it.priority in ("high", "mid", "low") else "mid",
                 _norm_month(it.month_tag)),
            ).fetchone()
            c.execute(
                "INSERT INTO task_events (room_id, task_id, user_id, action, payload) VALUES (%s, %s, %s, 'create', %s)",
                (room_id, row["id"], user_id, _json_payload({"title": title[:200], "batch": True})),
            )
            created += 1
        c.execute("UPDATE rooms SET version = version + 1 WHERE id = %s", (room_id,))
    return {"created": created}


@router.get("/stats")
def room_stats(ctx: dict = Depends(rooms.require_member)) -> dict:
    room_id = ctx["room"]["id"]
    with db.conn() as c:
        tasks = _room_tasks(c, room_id)
        members = _members_map(c, room_id)
    by_member: dict[int, dict] = {}
    for uid in members:
        by_member[uid] = {"created": 0, "completed": 0, "overdue": 0}
    today = date.today()
    for t in tasks:
        by_member[t["creator_id"]]["created"] += 1
        if t["status"] == "done" and t["completed_by"] in by_member:
            by_member[t["completed_by"]]["completed"] += 1
        if t["status"] != "done" and t["due_date"] and t["due_date"] < today:
            if t["assignee_id"] in by_member:
                by_member[t["assignee_id"]]["overdue"] += 1
            elif t["creator_id"] in by_member:
                by_member[t["creator_id"]]["overdue"] += 1
    return {
        "stats": _stats(tasks),
        "by_member": [
            {
                "user_id": uid,
                "username": members[uid]["username"],
                "display_name": members[uid]["display_name"],
                "color": members[uid]["color"],
                **counts,
            }
            for uid, counts in by_member.items()
        ],
    }


def _norm_month(value: str | None) -> str | None:
    import re
    if not value:
        return None
    v = str(value).strip()
    return v if re.match(r"^\d{4}-(0[1-9]|1[0-2])$", v) else None


def _json_payload(obj: dict):
    import json
    return json.dumps(obj, ensure_ascii=False)
