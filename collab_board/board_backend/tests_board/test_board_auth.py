"""认证：注册 / 登录 / 重复用户名 / 限流 / me。"""
from __future__ import annotations

from tests_board.conftest import auth_headers, register


def test_register_login_flow(client):
    body = register(client, "boss_a")
    assert body["token"] and body["user"]["username"] == "boss_a"
    assert body["user"]["color"].startswith("#")  # 注册自动分配颜色

    r = client.post("/api/auth/login", json={"username": "boss_a", "password": "password123"})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "boss_a"

    r = client.post("/api/auth/login", json={"username": "boss_a", "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["detail"] == "用户名或密码错误"  # 统一文案，不泄露哪项错


def test_register_validation(client):
    r = client.post("/api/auth/register", json={"username": "ab", "password": "password123"})
    assert r.status_code == 400  # 用户名 <3 位
    r = client.post("/api/auth/register", json={"username": "valid_user", "password": "short"})
    assert r.status_code == 400  # 密码 <8 位
    register(client, "dup_user")
    r = client.post("/api/auth/register", json={"username": "dup_user", "password": "password123"})
    assert r.status_code == 400  # 重复用户名


def test_login_rate_limit(client):
    for _ in range(10):
        r = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever-long"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever-long"})
    assert r.status_code == 429  # 第 11 次被限流


def test_me_and_patch(client):
    body = register(client, "me_user")
    headers = auth_headers(body["token"])
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200 and r.json()["username"] == "me_user"

    r = client.patch("/api/auth/me", json={"display_name": "张老板", "color": "#3D5A80"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["display_name"] == "张老板" and r.json()["color"] == "#3D5A80"

    r = client.patch("/api/auth/me", json={"color": "red"}, headers=headers)
    assert r.status_code == 400  # 非法颜色格式


def test_token_required(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
