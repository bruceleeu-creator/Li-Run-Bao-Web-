"""任务：CRUD + version 递增 + unchanged 短路 + stats 口径 + batch 上限。"""
from __future__ import annotations

from datetime import date, timedelta

from tests_board.conftest import auth_headers, make_room, register


def _setup(client):
    boss = register(client, "t_boss")
    room = make_room(client, boss["token"], "任务测试")
    staff = register(client, "t_staff")
    client.post(f"/api/rooms/{room['id']}/join",
                json={"invite_code": room["invite_code"]},
                headers=auth_headers(staff["token"]))
    return boss, staff, room


def _board(client, token, room_id, version=-1):
    return client.get(f"/api/rooms/{room_id}/board", params={"version": version},
                      headers=auth_headers(token)).json()


def test_task_crud_and_version(client):
    boss, staff, room = _setup(client)
    h = auth_headers(boss["token"])

    v0 = _board(client, boss["token"], room["id"])["version"]
    r = client.post(f"/api/rooms/{room['id']}/tasks",
                    json={"title": "核销 3 月广宣发票", "priority": "high"}, headers=h)
    assert r.status_code == 200
    task = r.json()["task"]
    assert task["status"] == "todo" and task["creator_name"]

    v1 = _board(client, boss["token"], room["id"])["version"]
    assert v1 == v0 + 1  # version 递增

    # 相同 version 短路
    assert _board(client, boss["token"], room["id"], version=v1)["unchanged"] is True

    # status → done 记 completed
    r = client.patch(f"/api/rooms/{room['id']}/tasks/{task['id']}",
                     json={"status": "done"}, headers=h)
    assert r.status_code == 200
    assert r.json()["task"]["completed_at"]

    # done → doing 清空完成信息
    r = client.patch(f"/api/rooms/{room['id']}/tasks/{task['id']}",
                     json={"status": "doing"}, headers=h)
    assert r.json()["task"]["completed_at"] is None

    # 未提供字段不被清空（PATCH 局部语义）
    r = client.patch(f"/api/rooms/{room['id']}/tasks/{task['id']}",
                     json={"priority": "low"}, headers=h)
    body = r.json()["task"]
    assert body["priority"] == "low" and body["title"] == "核销 3 月广宣发票"

    # 删除
    r = client.delete(f"/api/rooms/{room['id']}/tasks/{task['id']}", headers=h)
    assert r.status_code == 200
    assert all(t["id"] != task["id"] for t in _board(client, boss["token"], room["id"])["tasks"])


def test_board_visible_to_both_members(client):
    boss, staff, room = _setup(client)
    client.post(f"/api/rooms/{room['id']}/tasks", json={"title": "共享看板任务"},
                headers=auth_headers(staff["token"]))
    for token in (boss["token"], staff["token"]):
        board = _board(client, token, room["id"])
        assert board["stats"]["total"] == 1
        assert board["members"] and board["stats"]["completion"] == 0.0


def test_stats_overdue_and_due_today(client):
    boss, _, room = _setup(client)
    h = auth_headers(boss["token"])
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    for i, due in enumerate((yesterday, today, tomorrow)):
        client.post(f"/api/rooms/{room['id']}/tasks",
                    json={"title": f"任务{i}", "due_date": due}, headers=h)
    # 已完成的逾期任务：create 不收 status（契约），用 patch 置为已完成
    done = client.post(f"/api/rooms/{room['id']}/tasks",
                       json={"title": "已完成的逾期任务", "due_date": yesterday}, headers=h).json()["task"]
    client.patch(f"/api/rooms/{room['id']}/tasks/{done['id']}",
                 json={"status": "done"}, headers=h)
    stats = client.get(f"/api/rooms/{room['id']}/stats", headers=h).json()["stats"]
    assert stats["total"] == 4
    assert stats["overdue"] == 1  # 只有未完成的昨天到期算逾期
    assert stats["due_today"] == 1


def test_assignee_must_be_member(client):
    boss, _, room = _setup(client)
    r = client.post(f"/api/rooms/{room['id']}/tasks",
                    json={"title": "非法负责人", "assignee_id": 99999},
                    headers=auth_headers(boss["token"]))
    assert r.status_code == 400


def test_batch_limit(client):
    boss, _, room = _setup(client)
    h = auth_headers(boss["token"])
    items = [{"title": f"批量任务{i}"} for i in range(200)]
    r = client.post(f"/api/rooms/{room['id']}/tasks/batch", json={"items": items}, headers=h)
    assert r.status_code == 200 and r.json()["created"] == 200
    items.append({"title": "超限"})
    r = client.post(f"/api/rooms/{room['id']}/tasks/batch", json={"items": items}, headers=h)
    assert r.status_code == 400  # 201 条超限


def test_task_validation(client):
    boss, _, room = _setup(client)
    h = auth_headers(boss["token"])
    r = client.post(f"/api/rooms/{room['id']}/tasks", json={"title": "  "}, headers=h)
    assert r.status_code == 400  # 空标题
    r = client.post(f"/api/rooms/{room['id']}/tasks",
                    json={"title": "x", "priority": "urgent"}, headers=h)
    assert r.status_code == 400
    r = client.patch(f"/api/rooms/{room['id']}/tasks/1", json={"status": "paused"}, headers=h)
    assert r.status_code == 400
