# 演示部署（docs/design-v2.md 7.2）：mine-sim、actions、sentinel 共用的 Python 服务镜像，不同的启动命令。
#   构建上下文是仓库根目录（docker/compose.yml）。
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
WORKDIR /app
# 只装运行的三个服务（模拟矿井、guard、监听器）及其依赖（按 uv.lock 锁定版本）
COPY mcp /app/mcp
RUN cd /app/mcp && export UV_PROJECT_ENVIRONMENT=/opt/venv \
    && uv sync --frozen --no-dev --no-editable --package rescue-mine-data-sim \
    && uv sync --frozen --no-dev --no-editable --inexact --package rescue-actions \
    && uv sync --frozen --no-dev --no-editable --inexact --package rescue-sentinel \
    && rm -rf /root/.cache
COPY scenarios /app/scenarios
ENV PATH=/opt/venv/bin:$PATH \
    RESCUE_SCENARIO_DIR=/app/scenarios \
    RESCUE_MCP_HOST=0.0.0.0 \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
