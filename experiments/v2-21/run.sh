#!/usr/bin/env bash
# 验证清单 #21：在容器里运行 DSH（第二版主预设），宿主机跑 MCP 服务。
#   experiments/v2-21/run.sh [default|unconfined]     # default：Docker 默认 seccomp/AppArmor/masked paths（DSH 用 Landlock）；unconfined：三者都放开（bwrap 才能用）
# 网页端 http://127.0.0.1:3084（带 token 的地址见输出）。仓库按原路径挂进容器。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
. scripts/lib/env.sh && load_env .env
MODE="${1:-default}"
H="$REPO/.dsh-home/p21-home"
if [ ! -d "$H/profiles/rescue" ]; then
  mkdir -p "$H/profiles" && cp -a .dsh-home/profiles/rescue "$H/profiles/" && cp -a .dsh-home/.credentials.yaml "$H/"
  ln -sfn "$REPO/dsh-bundle" "$H/profiles/rescue/node_modules/@rescue/dsh-bundle"
fi
export RESCUE_MCP_TOKEN=shared-t RESCUE_MCP_MINE_DATA_TOKEN=ro-t RESCUE_MCP_ACTIONS_TOKEN=act-t RESCUE_SIM_CONTROL_TOKEN=ctl-t
scripts/mcp-up.sh >/dev/null 2>&1
SEC=()
[ "$MODE" = unconfined ] && SEC=(--security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined)
mkdir -p var/eval/v2-21/records
exec docker run --rm --name rescue-dsh-probe --network host --user 1000:1000 "${SEC[@]}" \
  -v "$REPO:$REPO" \
  -e RESCUE_HOME="$REPO" -e DSH_HOME="$H" -e RESCUE_RECORD_DIR="$REPO/var/eval/v2-21/records" -e RESCUE_SKILL_PYTHON=python3 \
  -e DEEPSEEK_API_KEY -e RESCUE_MCP_MINE_DATA_TOKEN -e RESCUE_MCP_ACTIONS_TOKEN \
  rescue-dsh-probe dsh rescue --patch "$REPO/profiles/v2.patch.yml" --patch "$REPO/experiments/v2-21/container.patch.yml" --no-open --port 3084
