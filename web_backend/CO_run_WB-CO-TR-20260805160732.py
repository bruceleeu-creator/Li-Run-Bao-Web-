"""利润宝 · Web 启动器（仅本机回环）。

启动 uvicorn 于 127.0.0.1，并通过 web_frontend/dist 提供静态前端。
"""

import importlib
from pathlib import Path

from fastapi.staticfiles import StaticFiles

# 文件名含项目智能体标识连字符，不是合法模块标识符，须用 importlib 加载
_app_module = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
create_app = _app_module.create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = PROJECT_ROOT / "web_frontend" / "dist"
DEFAULT_PORT = 8765


def server_settings() -> dict[str, str]:
    """回环绑定声明，供验收测试断言使用。"""
    return {"host": "127.0.0.1", "port": DEFAULT_PORT}


def mount_static(app) -> None:
    """将构建产物挂载为根路径静态文件；缺少构建产物时保持仅 API 服务。"""
    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


def main() -> None:
    settings = server_settings()
    app = create_app()
    mount_static(app)
    import uvicorn

    uvicorn.run(app, host=settings["host"], port=int(settings["port"]))


if __name__ == "__main__":
    main()
