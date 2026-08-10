"""利润宝 · 本机 Web 服务（FastAPI）。

仅监听 127.0.0.1，不提供局域网或公网访问；前端只与本地 API 交互。
领域计算全部复用 core/，本层只做输入输出适配。
"""

import importlib

from fastapi import FastAPI

_import_module = importlib.import_module("web_backend.CO_import_WB-CO-TR-20260805160732")
_ai_module = importlib.import_module("web_backend.CO_ai_route_WB-CO-TR-20260805160732")
_budget_module = importlib.import_module("web_backend.CO_budget_WB-CO-TR-20260805160732")
_diagnosis_module = importlib.import_module("web_backend.CO_diagnosis_WB-CO-TR-20260805160732")
_interaction_module = importlib.import_module("web_backend.CO_interaction_WB-CO-TR-20260805160732")


def create_app() -> FastAPI:
    """构建 FastAPI 应用。功能路由经此注册。"""
    app = FastAPI(title="利润宝本地服务", version="0.1.0")
    app.include_router(_import_module.router)
    app.include_router(_ai_module.router)
    app.include_router(_budget_module.router)
    app.include_router(_diagnosis_module.router)
    app.include_router(_interaction_module.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "bind": "127.0.0.1"}

    return app
