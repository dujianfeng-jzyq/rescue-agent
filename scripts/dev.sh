#!/usr/bin/env bash
# 开发启动：MCP 服务（含模拟矿井）+ DSH 网页端（rescue 配置档）。
#
#   scripts/dev.sh                       默认场景（.env 里的 RESCUE_SIM_SCENARIO）
#   scripts/dev.sh --local-llm           改用本地化部署的大模型（profiles/local-llm.patch.yml）
#   scripts/dev.sh --v2                  第二版主预设（profiles/v2.patch.yml，P4 之前是可选叠加）
#   RESCUE_SIM_SCENARIO=s4-escalation scripts/dev.sh
#
# 退出 DSH 后 MCP 服务继续运行，用 scripts/mcp-down.sh 停止。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/lib/env.sh && load_env .env   # 已 export 的变量优先于 .env
export RESCUE_HOME="${RESCUE_HOME:-$ROOT}"
export DSH_HOME="${DSH_HOME:-$ROOT/.dsh-home}"   # DSH_* 必须 export，.env 里的 DSH 不认
export RESCUE_SIM_CONTROL_URL="${RESCUE_SIM_CONTROL_URL-http://127.0.0.1:8101/sim}"
mkdir -p var/audit

scripts/mcp-up.sh

EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --local-llm)
      : "${RESCUE_LLM_BASE_URL:?未设置 RESCUE_LLM_BASE_URL}" "${RESCUE_LLM_MODEL:?未设置 RESCUE_LLM_MODEL}"
      EXTRA+=(--patch profiles/local-llm.patch.yml); shift ;;
    --v2)
      EXTRA+=(--patch profiles/v2.patch.yml); shift ;;
    *) break ;;
  esac
done

exec dsh rescue "${EXTRA[@]}" --no-open "$@"   # --patch 是启动器参数，必须在 app 参数之前
