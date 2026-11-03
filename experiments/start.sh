#!/usr/bin/env bash
# 实验 0001：按组启动 MCP 服务和 DSH 网页端（前台运行 DSH，退出后 MCP 服务继续运行，用 scripts/mcp-down.sh 停止）。
#
#   experiments/start.sh a|b|c
#
# 与 make dev-eval 相同，指令台账、快照、值守事件、审计写到 var/eval/exp-0001/，不写复盘用的 var/。
# 令牌：export RESCUE_MCP_TOKEN=<随机值> 后再运行，模拟生产配置（本机 .env 里的令牌是空的，服务不校验）。
set -euo pipefail
G="${1:?用法：experiments/start.sh a|b|c}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/lib/env.sh && load_env .env
E="$ROOT/var/eval/exp-0001"
mkdir -p "$E/snapshots" "$E/monitor" "$E/audit"
export RESCUE_HOME="$ROOT" DSH_HOME="$ROOT/.dsh-home"
export RESCUE_SIM_CONTROL_URL="${RESCUE_SIM_CONTROL_URL-http://127.0.0.1:8101/sim}"
export RESCUE_ACTIONS_LEDGER="$E/actions-ledger.jsonl" RESCUE_SNAPSHOT_DIR="$E/snapshots"
export RESCUE_MONITOR_DIR="$E/monitor" RESCUE_AUDIT_LOG="$E/audit/tool-actions.jsonl"
unset ALL_PROXY all_proxy

scripts/mcp-up.sh

case "$G" in
  a) exec dsh rescue --no-open ;;
  b|c) exec dsh rescue --patch "experiments/patches/exp-$G.patch.yml" --no-open ;;
  *) echo "未知组：$G" >&2; exit 2 ;;
esac
