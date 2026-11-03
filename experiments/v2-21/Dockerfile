# 验证清单 #21：DSH 的 bash 沙箱在容器里能否工作（不是演示部署镜像，P5 另做）。
#   docker build -t rescue-dsh-probe experiments/v2-21
# 仓库按原路径挂进容器（--network host，用户 uid 1000 = node），见 run.sh。
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap python3 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN npm i -g @deepseek-ai/dsh@0.2.1-rc.4 && npm cache clean --force
# 探测脚本用 curl 看网络（放在 DSH 之后，改动不触发重装）
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
USER node
WORKDIR /home/node
