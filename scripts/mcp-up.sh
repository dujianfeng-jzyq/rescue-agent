#!/usr/bin/env bash
# 启动全部 MCP 服务（后台运行），日志在 var/log/，PID 在 var/run/。
#
#   scripts/mcp-up.sh            启动全部
#   scripts/mcp-up.sh kb actions 只启动指定服务
#
# 默认用 `uv run python` 启动；可设置 RESCUE_PYTHON 覆盖（例如已激活的虚拟环境里的 python）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/lib/env.sh && load_env .env   # 已 export 的变量优先于 .env
export RESCUE_HOME="${RESCUE_HOME:-$ROOT}"
export RESCUE_ACTIONS_LEDGER="${RESCUE_ACTIONS_LEDGER:-$ROOT/var/actions-ledger.jsonl}"

PY="${RESCUE_PYTHON:-uv run --project $ROOT/mcp python}"
mkdir -p var/log var/run

declare -A MODULES=(
  [mine_data]=rescue_mine_data_sim
  [ventnet]=rescue_ventnet
  [fire_model]=rescue_fire_model
  [dispatch]=rescue_dispatch
  [kb]=rescue_kb
  [actions]=rescue_actions
)
ORDER=(mine_data ventnet fire_model dispatch kb actions)
TARGETS=("${@:-${ORDER[@]}}")

# 接入真实矿井时，用 RESCUE_MINE_DATA_MODULE 指向该矿的适配器模块
MODULES[mine_data]="${RESCUE_MINE_DATA_MODULE:-${MODULES[mine_data]}}"

for key in "${TARGETS[@]}"; do
  mod="${MODULES[$key]:?未知服务 $key}"
  pidfile="var/run/$key.pid"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  $key 已在运行（PID $(cat "$pidfile")）"
    continue
  fi
  nohup $PY -m "$mod" >"var/log/$key.log" 2>&1 &
  echo $! >"$pidfile"
  echo "  启动 $key（$mod，PID $!）"
done

# 等待健康检查
for key in "${TARGETS[@]}"; do
  var="RESCUE_$(echo "$key" | tr '[:lower:]' '[:upper:]')_PORT"
  case $key in
    mine_data) port=${!var:-8101} ;; ventnet) port=${!var:-8102} ;; fire_model) port=${!var:-8103} ;;
    dispatch) port=${!var:-8104} ;; kb) port=${!var:-8105} ;; actions) port=${!var:-8106} ;;
  esac
  for _ in $(seq 1 50); do
    curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break
    sleep 0.2
  done
  if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
    echo "  ✓ $key  http://127.0.0.1:$port/mcp"
  else
    echo "  ✗ $key 未就绪，查看 var/log/$key.log" >&2
    exit 1
  fi
done
