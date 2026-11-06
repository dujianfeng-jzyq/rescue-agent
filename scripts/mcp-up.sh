#!/usr/bin/env bash
# 启动 MCP 服务（后台运行），日志在 var/log/，PID 在 var/run/。
#
#   scripts/mcp-up.sh            启动：mine_data（模拟矿井）、actions（guard 守卫）、sentinel（常驻监听器，不是 MCP 服务，没有端口）
#                                RESCUE_SENTINEL=0 不启动监听器（智能体评测用：评测不要被监听器的告警唤醒）
#
# 默认用 `uv run python` 启动；可设置 RESCUE_PYTHON 覆盖（例如已激活的虚拟环境里的 python）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/lib/env.sh && load_env .env   # 已 export 的变量优先于 .env
export RESCUE_HOME="${RESCUE_HOME:-$ROOT}"
export RESCUE_ACTIONS_LEDGER="${RESCUE_ACTIONS_LEDGER:-$ROOT/var/actions-ledger.jsonl}"
export RESCUE_INCIDENT_DIR="${RESCUE_INCIDENT_DIR:-$ROOT/var/incidents}"   # 事件库：监听器写，DSH 事件插件读写，模拟矿井控制台只读

PY="${RESCUE_PYTHON:-uv run --project $ROOT/mcp python}"
mkdir -p var/log var/run

declare -A MODULES=(
  [mine_data]=rescue_mine_data_sim
  [actions]=rescue_actions
  [sentinel]=rescue_sentinel
)
ORDER=(mine_data actions)
[ "${RESCUE_SENTINEL:-1}" = 0 ] || ORDER+=(sentinel)
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
  if [ "$key" = sentinel ]; then   # 没有端口：看进程还在不在
    sleep 1
    if kill -0 "$(cat var/run/sentinel.pid)" 2>/dev/null; then echo "  ✓ sentinel  事件库 $RESCUE_INCIDENT_DIR"; continue; fi
    echo "  ✗ sentinel 未就绪，查看 var/log/sentinel.log" >&2; exit 1
  fi
  var="RESCUE_$(echo "$key" | tr '[:lower:]' '[:upper:]')_PORT"
  case $key in
    mine_data) port=${!var:-8101} ;; actions) port=${!var:-8106} ;;
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
