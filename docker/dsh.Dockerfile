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
# 依赖和代码分开装，依赖层全部放在代码层前面：只改代码时第三方包、node_modules 都复用（任何一层变了，它后面的层都要重做）。
# 1) Python 第三方包：技能脚本只能用 skills/requirements.txt 里的包；技能自检用 rescue_contract，
#    这里只按 contract 的 pyproject.toml 装它的依赖和构建后端 hatchling，contract 本身在后面装。
COPY skills/requirements.txt /tmp/skills-requirements.txt
COPY mcp/contract/pyproject.toml /tmp/contract-pyproject.toml
RUN python -c "import tomllib; d = tomllib.load(open('/tmp/contract-pyproject.toml', 'rb')); print('\n'.join(d['project']['dependencies'] + d['build-system']['requires']))" > /tmp/contract-requirements.txt \
    && pip install --no-cache-dir -r /tmp/skills-requirements.txt -r /tmp/contract-requirements.txt
# 2) bundle 的 node 依赖：只按 package.json 和锁文件装（node_modules、lib 不进构建上下文，见 .dockerignore）
WORKDIR /app/dsh-bundle
COPY dsh-bundle/package.json dsh-bundle/pnpm-lock.yaml dsh-bundle/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile && rm -rf /root/.cache /root/.local/share/pnpm/store
# 3) 代码：contract（--no-deps --no-build-isolation，不联网）、bundle 构建（构建后装进 rescue 配置档：配置档在入口脚本里建，DSH_HOME 是数据卷）
COPY mcp/contract /tmp/contract
RUN pip install --no-cache-dir --no-deps --no-build-isolation /tmp/contract
COPY dsh-bundle ./
RUN pnpm build
WORKDIR /app
# 技能（运行时只读挂载覆盖这里的副本；镜像构建时跑一遍自检）
COPY skills /app/skills
RUN PYTHONDONTWRITEBYTECODE=1 python -m rescue_contract.skills check /app/skills
COPY docker/dsh-entrypoint.sh docker/dsh-health.sh docker/container.patch.yml /app/docker/
ENV RESCUE_HOME=/app RESCUE_SKILL_PYTHON=python3 DSH_HOME=/home/node/.dsh-home HOME=/home/node
RUN useradd -u 1000 -m -d /home/node node 2>/dev/null || true; mkdir -p /home/node/Documents && chown -R 1000:1000 /home/node
USER 1000:1000
ENTRYPOINT ["/app/docker/dsh-entrypoint.sh"]
