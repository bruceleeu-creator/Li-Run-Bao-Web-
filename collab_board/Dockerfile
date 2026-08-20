# 协同看板服务镜像（腾讯云轻量服务器 Docker Compose 自部署）
FROM python:3.12-slim

WORKDIR /app

COPY board_backend/requirements-board.txt /app/board_backend/requirements-board.txt
RUN pip install --no-cache-dir -r /app/board_backend/requirements-board.txt

# 前端构建产物随包上传（服务器不装 Node）
COPY board_backend /app/board_backend
COPY board_frontend/dist /app/board_frontend/dist

WORKDIR /app/board_backend
EXPOSE 8080

CMD ["python", "-c", "import importlib; app = importlib.import_module('board_backend.CO_app_WB-CO-TR-20260820').create_app(); import uvicorn; uvicorn.run(app, host='0.0.0.0', port=8080)"]
