# 字体：DSH 的 Office 预览（LibreOffice WASM）没有可用字体就拒绝转换，网页端预览 .docx 报告会显示“Office previews are unavailable”。
# 演示部署（docs/design-v2.md 7.2）：DSH 0.2.1-rc.4 + 本项目 bundle + 技能脚本的 Python 运行时。
#   构建上下文是仓库根目录（docker/compose.yml）。沙箱在容器里用 Landlock（Docker 默认安全配置，验证清单 #21）。
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && apt-get update && apt-get install -y --no-install-recommends bubblewrap ca-certificates curl fonts-wqy-microhei fonts-dejavu-core && rm -rf /var/lib/apt/lists/* \
    # pnpm 直接装进镜像，不用 corepack：corepack 没有 packageManager 字段时每次都联网查 latest，运行期网络不通就装不上 bundle（见 dsh-entrypoint.sh）
    && npm i -g @deepseek-ai/dsh@0.2.1-rc.4 pnpm@12.18.0 && npm cache clean --force
# 技能脚本只能用 skills/requirements.txt 里的包；技能自检用 rescue_contract
COPY skills/requirements.txt /tmp/skills-requirements.txt
COPY mcp/contract /tmp/contract
RUN pip install --no-cache-dir -r /tmp/skills-requirements.txt /tmp/contract
# bundle：构建后装进 rescue 配置档（配置档在入口脚本里建，DSH_HOME 是数据卷）
WORKDIR /app
COPY dsh-bundle /app/dsh-bundle
RUN cd /app/dsh-bundle && rm -rf node_modules lib && pnpm install --frozen-lockfile && pnpm build && rm -rf /root/.cache /root/.local/share/pnpm/store
# 技能（运行时只读挂载覆盖这里的副本；镜像构建时跑一遍自检）
COPY skills /app/skills
RUN PYTHONDONTWRITEBYTECODE=1 python -m rescue_contract.skills check /app/skills
COPY docker/dsh-entrypoint.sh docker/dsh-health.sh docker/container.patch.yml /app/docker/
ENV RESCUE_HOME=/app RESCUE_SKILL_PYTHON=python3 DSH_HOME=/home/node/.dsh-home HOME=/home/node
RUN useradd -u 1000 -m -d /home/node node 2>/dev/null || true; mkdir -p /home/node/Documents && chown -R 1000:1000 /home/node
USER 1000:1000
ENTRYPOINT ["/app/docker/dsh-entrypoint.sh"]
