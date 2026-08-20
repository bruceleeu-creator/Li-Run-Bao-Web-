"""利润宝 · Web 启动器。

默认仅绑定本机回环 127.0.0.1:8765；容器/服务器部署通过环境变量
LRB_HOST / LRB_PORT 覆盖绑定地址与端口（Docker compose 内设 0.0.0.0）。
"""

import importlib
import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles

# 文件名含项目智能体标识连字符，不是合法模块标识符，须用 importlib 加载
_app_module = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
create_app = _app_module.create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = PROJECT_ROOT / "web_frontend" / "dist"
DEFAULT_PORT = 8765


def server_settings() -> dict[str, str]:
    """绑定声明（默认 127.0.0.1 回环；LRB_HOST/LRB_PORT 可覆盖，供验收测试断言）。"""
    return {
        "host": os.environ.get("LRB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("LRB_PORT", DEFAULT_PORT)),
    }


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
