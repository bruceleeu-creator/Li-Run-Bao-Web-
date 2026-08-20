"""协同看板 · FastAPI 应用工厂。

独立服务（不依赖 core/ 与 web_backend/）：/api 路由 + 前端构建产物静态托管
（单服务，少一跳）+ /api/health。启动时幂等建表。

模块名含智能体标识连字符，用 importlib 加载（与主项目同一约定）。
"""
from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("collab_board")

auth = importlib.import_module("board_backend.CO_auth_WB-CO-TR-20260820")
rooms = importlib.import_module("board_backend.CO_rooms_WB-CO-TR-20260820")
tasks = importlib.import_module("board_backend.CO_tasks_WB-CO-TR-20260820")
template = importlib.import_module("board_backend.CO_template_WB-CO-TR-20260820")
db = importlib.import_module("board_backend.CO_db_WB-CO-TR-20260820")

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "board_frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="利润宝协同看板", version="1.0.0")
    app.include_router(auth.router)
    app.include_router(rooms.router)
    app.include_router(tasks.router)
    app.include_router(template.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "db": db.health(), "env": os.environ.get("BOARD_ENV", "dev")}

    @app.on_event("startup")
    def _startup() -> None:
        try:
            db.init_schema()
            logger.info("schema 初始化完成（幂等）")
        except Exception:
            logger.exception("schema 初始化失败（检查 DATABASE_URL）")
            raise

    # 前端构建产物挂根路径（单服务）；无产物时仅 API
    if _FRONTEND_DIST.exists() and (_FRONTEND_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
    return app


def main() -> None:
    """容器入口：uvicorn 0.0.0.0:8080。"""
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("BOARD_PORT", "8080")))


if __name__ == "__main__":
    main()
