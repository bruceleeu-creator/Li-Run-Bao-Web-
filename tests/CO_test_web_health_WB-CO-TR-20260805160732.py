"""本机 Web 服务健康检查的边界契约。"""

from importlib import import_module

from fastapi.testclient import TestClient


def test_health_reports_local_only_service():
    """服务不应宣称可被局域网或公网访问。"""
    module = import_module("web_backend.CO_app_WB-CO-TR-20260805160732")

    response = TestClient(module.create_app()).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "bind": "127.0.0.1"}


def test_web_server_is_loopback_only():
    """启动器必须只绑定 127.0.0.1 回环地址。"""
    run_module = import_module("web_backend.CO_run_WB-CO-TR-20260805160732")

    assert run_module.server_settings() == {"host": "127.0.0.1", "port": 8765}

