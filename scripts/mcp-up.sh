#!/usr/bin/env bash
# 启动 MCP 服务（后台运行），日志在 var/log/，PID 在 var/run/。
#
#   scripts/mcp-up.sh            启动第二版的服务：mine_data（模拟矿井）、actions（指令发布）
#   scripts/mcp-up.sh fire_model 单独启动第一版的服务（P4 起停用：ventnet、fire_model、dispatch、kb、workflow、monitor
#                                的功能已迁入技能和 DSH 插件，代码暂留作一致性测试的对照，docs/design-v2.md 第 8 节）
#
# 默认用 `uv run python` 启动；可设置 RESCUE_PYTHON 覆盖（例如已激活的虚拟环境里的 python）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/lib/env.sh && load_env .env   # 已 export 的变量优先于 .env
export RESCUE_HOME="${RESCUE_HOME:-$ROOT}"
export RESCUE_ACTIONS_LEDGER="${RESCUE_ACTIONS_LEDGER:-$ROOT/var/actions-ledger.jsonl}"
export RESCUE_SNAPSHOT_DIR="${RESCUE_SNAPSHOT_DIR:-$ROOT/var/snapshots}"   # workflow 写、actions 和 kb 读，必须同一目录
export RESCUE_MONITOR_DIR="${RESCUE_MONITOR_DIR:-$ROOT/var/monitor}"         # 值守事件

PY="${RESCUE_PYTHON:-uv run --project $ROOT/mcp python}"
mkdir -p var/log var/run

declare -A MODULES=(
  [mine_data]=rescue_mine_data_sim
  [ventnet]=rescue_ventnet
  [fire_model]=rescue_fire_model
  [dispatch]=rescue_dispatch
  [kb]=rescue_kb
  [actions]=rescue_actions
  [workflow]=rescue_workflow
  [monitor]=rescue_workflow.monitor   # 值守进程（不是 MCP 服务，没有端口）
)
ORDER=(mine_data actions)   # 第二版只运行这两个（design-v2 7.2）；其余为第一版，按名字单独启动
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
  if [ "$key" = monitor ]; then   # 值守进程没有端口：确认进程还活着
    sleep 1
    if kill -0 "$(cat var/run/monitor.pid)" 2>/dev/null; then echo "  ✓ monitor  值守事件 $RESCUE_MONITOR_DIR/events.jsonl"; continue; fi
    echo "  ✗ monitor 未就绪，查看 var/log/monitor.log" >&2
    exit 1
  fi
  var="RESCUE_$(echo "$key" | tr '[:lower:]' '[:upper:]')_PORT"
  case $key in
    mine_data) port=${!var:-8101} ;; ventnet) port=${!var:-8102} ;; fire_model) port=${!var:-8103} ;;
    dispatch) port=${!var:-8104} ;; kb) port=${!var:-8105} ;; actions) port=${!var:-8106} ;;
    workflow) port=${!var:-8107} ;;
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
