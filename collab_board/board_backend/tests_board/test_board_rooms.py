"""房间：建房 / 凭码加入 / 码错锁定 / 重置码 / 成员管理 / 权限隔离。"""
from __future__ import annotations

from tests_board.conftest import auth_headers, make_room, register


def _join(client, token: str, room_id: int, code: str):
    return client.post(f"/api/rooms/{room_id}/join", json={"invite_code": code},
                       headers=auth_headers(token))


def test_create_room_and_invite_join(client):
    boss = register(client, "owner_boss")
    room = make_room(client, boss["token"], "落地跟踪")
    assert room["invite_code"] and len(room["invite_code"]) == 8

    staff = register(client, "staff_one")
    r = _join(client, staff["token"], room["id"], "WRONGCODE")
    assert r.status_code == 400  # 码错

    r = _join(client, staff["token"], room["id"], room["invite_code"])
    assert r.status_code == 200 and r.json()["role"] == "member"

    # 双方看到同一房间
    for token in (boss["token"], staff["token"]):
        rooms = client.get("/api/rooms", headers=auth_headers(token)).json()["rooms"]
        assert any(x["id"] == room["id"] and x["name"] == "落地跟踪" for x in rooms)


def test_join_lockout_after_four_fails(client):
    boss = register(client, "owner_lock")
    room = make_room(client, boss["token"])
    staff = register(client, "staff_lock")
    for i in range(4):
        r = _join(client, staff["token"], room["id"], "BADCODE1")
        assert r.status_code == 400
    r = _join(client, staff["token"], room["id"], room["invite_code"])
    assert r.status_code == 429  # 第 5 次（即使码对）锁定提示


def test_invite_refresh_invalidates_old(client):
    boss = register(client, "owner_ref")
    room = make_room(client, boss["token"])
    staff = register(client, "staff_ref")
    r = _join(client, staff["token"], room["id"], room["invite_code"])
    assert r.status_code == 200

    new_user = register(client, "staff_late")
    r = client.post(f"/api/rooms/{room['id']}/invite/refresh", headers=auth_headers(boss["token"]))
    assert r.status_code == 200
    new_code = r.json()["invite_code"]
    assert new_code != room["invite_code"]

    # 旧码失效，新码可加入；原成员不受影响
    r = _join(client, new_user["token"], room["id"], room["invite_code"])
    assert r.status_code == 400
    r = _join(client, new_user["token"], room["id"], new_code)
    assert r.status_code == 200
    members = client.get(f"/api/rooms/{room['id']}/members",
                         headers=auth_headers(boss["token"])).json()["members"]
    assert len(members) == 3


def test_room_isolation_403(client):
    boss = register(client, "owner_iso")
    room = make_room(client, boss["token"])
    outsider = register(client, "outsider_x")
    for path in ("board", "members", "stats", "export.xlsx"):
        r = client.get(f"/api/rooms/{room['id']}/{path}", headers=auth_headers(outsider["token"]))
        assert r.status_code == 403, path
    r = client.post(f"/api/rooms/{room['id']}/tasks", json={"title": "x"},
                    headers=auth_headers(outsider["token"]))
    assert r.status_code == 403


def test_member_cannot_refresh_or_remove(client):
    boss = register(client, "owner_perm")
    room = make_room(client, boss["token"])
    staff = register(client, "staff_perm")
    _join(client, staff["token"], room["id"], room["invite_code"])
    r = client.post(f"/api/rooms/{room['id']}/invite/refresh", headers=auth_headers(staff["token"]))
    assert r.status_code == 403
    r = client.delete(f"/api/rooms/{room['id']}/members/1", headers=auth_headers(staff["token"]))
    assert r.status_code == 403


def test_remove_member_keeps_history(client):
    boss = register(client, "owner_rm")
    room = make_room(client, boss["token"])
    staff = register(client, "staff_rm")
    _join(client, staff["token"], room["id"], room["invite_code"])
    r = client.post(f"/api/rooms/{room['id']}/tasks",
                    json={"title": "员工建的任务"}, headers=auth_headers(staff["token"]))
    task_id = r.json()["task"]["id"]

    members = client.get(f"/api/rooms/{room['id']}/members",
                         headers=auth_headers(boss["token"])).json()["members"]
    staff_id = next(m["id"] for m in members if m["username"] == "staff_rm")
    r = client.delete(f"/api/rooms/{room['id']}/members/{staff_id}",
                      headers=auth_headers(boss["token"]))
    assert r.status_code == 200

    # 历史任务保留（creator 信息仍在），被移除者失去访问权
    board = client.get(f"/api/rooms/{room['id']}/board",
                       headers=auth_headers(boss["token"])).json()
    assert any(t["id"] == task_id and t["creator_name"] for t in board["tasks"])
    r = client.get(f"/api/rooms/{room['id']}/board", headers=auth_headers(staff["token"]))
    assert r.status_code == 403

    # 不能移除自己（负责人）
    boss_id = next(m["id"] for m in members if m["username"] == "owner_rm")
    r = client.delete(f"/api/rooms/{room['id']}/members/{boss_id}",
                      headers=auth_headers(boss["token"]))
    assert r.status_code == 400
