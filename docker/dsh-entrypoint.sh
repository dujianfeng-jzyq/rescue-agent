#!/bin/sh
# DSH 容器入口：第一次启动时在数据卷里建 rescue 配置档并装入 bundle，然后启动网页端。
# DSH 不接受通配地址，绑定本容器的地址；浏览器经宿主机端口访问，所以对外地址写宿主机的 127.0.0.1:端口。
set -eu
PORT="${DSH_PORT:-3080}"
PUBLIC_PORT="${DSH_PUBLIC_PORT:-13080}"
if [ ! -d "$DSH_HOME/profiles/rescue" ]; then
  mkdir -p "$DSH_HOME"
  dsh rescue --from-default-profile web --dump-config >/dev/null
  dsh plugin --profile rescue add /app/dsh-bundle >/dev/null
  # 用户层 patch：演示前已确认过预览声明（不弹窗）；缺省模型（DEMO_MODEL=提供方/模型）
  MODEL="${DEMO_MODEL:-deepseek-official/deepseek-flash}"
  cat >"$DSH_HOME/profiles/rescue/cordis.patch.yml" <<YAML
- id: ui-settings-general
  name: "@deepseek-ai/dsh-client-ui-settings-general"
  config:
    welcomeNoticeVersion: 2026-09-28.1
- id: agent-default-model
  name: "@deepseek-ai/dsh-agent-default-model"
  config:
    provider: ${MODEL%%/*}
    model: ${MODEL#*/}
    reasoningEffort: high
YAML
fi
IP="$(hostname -i | awk '{print $1}')"
exec dsh rescue --patch /app/docker/container.patch.yml --no-open --host "$IP" --port "$PORT" \
  --public-url "http://127.0.0.1:$PUBLIC_PORT/" --trusted-host "127.0.0.1:$PUBLIC_PORT" --trusted-host "localhost:$PUBLIC_PORT"
