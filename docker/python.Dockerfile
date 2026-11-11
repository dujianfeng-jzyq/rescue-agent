# 演示部署（docs/design-v2.md 7.2）：mine-sim、actions、sentinel 共用的 Python 服务镜像，不同的启动命令。
#   构建上下文是仓库根目录（docker/compose.yml）。
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
WORKDIR /app
# 只装运行的三个服务（模拟矿井、guard、监听器）及其依赖（按 uv.lock 锁定版本）。
#   依赖和代码分两层：先只复制工作区和各成员的 pyproject.toml、uv.lock，装第三方包（--no-install-workspace）；
#   再复制源码装工作区的包。只改代码时第三方包那层复用；uv 缓存挂在构建缓存里（不进镜像），构建后端不用每次重新下载。
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
WORKDIR /app/mcp
COPY mcp/pyproject.toml mcp/uv.lock ./
COPY mcp/contract/pyproject.toml contract/
COPY mcp/mine-data-sim/pyproject.toml mine-data-sim/
COPY mcp/actions/pyproject.toml actions/
COPY mcp/sentinel/pyproject.toml sentinel/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package rescue-mine-data-sim \
    && uv sync --frozen --no-dev --no-install-workspace --inexact --package rescue-actions \
    && uv sync --frozen --no-dev --no-install-workspace --inexact --package rescue-sentinel
COPY mcp ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --inexact --package rescue-mine-data-sim \
    && uv sync --frozen --no-dev --no-editable --inexact --package rescue-actions \
    && uv sync --frozen --no-dev --no-editable --inexact --package rescue-sentinel
WORKDIR /app
COPY scenarios /app/scenarios
ENV PATH=/opt/venv/bin:$PATH \
    RESCUE_SCENARIO_DIR=/app/scenarios \
    RESCUE_MCP_HOST=0.0.0.0 \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
