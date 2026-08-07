# GridMind 灵枢电网 — 后端容器镜像（D1）
# ---------------------------------------------------------------------------
# 纯后端镜像：前端（web/）为独立 Vite 构建产物，当前未打包进本镜像。
# 勘察结论：static/ 与 templates/ 均为空目录，api/main.py 未挂载静态资源
# （无 StaticFiles/mount），故不需要 multi-stage（node build + python serve）。
# 前端产物由 web/ 独立构建后经反向代理/CDN 分发，或后续按需追加 COPY。
#
# 构建：
#   docker build -t gridmind:test .
# 运行（单容器，MCP Server 由 api/main.py lifespan 连接）：
#   docker run -p 9900:9900 -p 9901:9901 --env-file .env gridmind:test
# ---------------------------------------------------------------------------
FROM python:3.13-slim

LABEL org.opencontainers.image.title="GridMind 灵枢电网" \
      org.opencontainers.image.description="GridMind 后端 API（FastAPI + LangGraph + MCP）" \
      org.opencontainers.image.version="1.5.1"

WORKDIR /app

# 系统依赖：ca-certificates（chromadb/onnxruntime 等二进制 wheel 在 slim 下通常
# 可直接安装；如遇编译缺失可追加 build-essential 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖清单以利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码（排除测试/前端/数据等非运行时文件）
COPY api/ api/
COPY core/ core/
COPY mcp_tools/ mcp_tools/
COPY prompts/ prompts/

# 数据目录（SQLite / Chroma 持久化，配合 docker-compose 卷挂载）
RUN mkdir -p /app/data

# 端口：9900 = FastAPI，9901 = MCP Server
EXPOSE 9900 9901

# 启动 FastAPI（MCP Server 连接逻辑在 api/main.py lifespan 内；
# 如需独立 MCP 进程可覆盖 CMD）
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "9900"]
