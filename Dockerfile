# 利润宝 · 主应用镜像（腾讯云轻量服务器 Docker Compose 自部署，2026-08-20）
# 本机回环运行保持不变；容器内通过 LRB_HOST=0.0.0.0 对外提供服务
FROM python:3.12-slim

WORKDIR /app

# apt/pip 均走腾讯云镜像：服务器直连 deb.debian.org / PyPI 极慢（曾卡 10+ 分钟）
RUN sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g; s|security.debian.org|mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true \
 && apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple -r /app/requirements.txt

# 应用代码：web_backend 依赖 core/（领域引擎）/ demo_output/cases/（案例包）/ data/（样例数据）
COPY web_backend /app/web_backend
COPY core /app/core
COPY data /app/data
COPY demo_output/cases /app/demo_output/cases
COPY web_frontend/dist /app/web_frontend/dist

# 连字符模块名：以 /app 为 sys.path 根 + importlib 加载约定（同 collab_board）
ENV PYTHONPATH=/app
ENV LRB_HOST=0.0.0.0
ENV LRB_PORT=8765
EXPOSE 8765

CMD ["python", "-m", "web_backend.CO_run_WB-CO-TR-20260805160732"]
