#!/usr/bin/env bash
# 演示环境（Docker，docs/design-v2.md 7.2）。
#
#   scripts/demo.sh up       构建并启动全部服务，等健康检查通过，打印网页端和控制台地址（make demo-up）
#   scripts/demo.sh down     停止（数据保留在 var/demo/）（make demo-down）
#   scripts/demo.sh reset    停止、清空 var/demo/ 下的演示数据（记录、台账、审计、会话），再启动；模拟矿井回到常态（make demo-reset）
#   scripts/demo.sh logs     跟踪日志
#
# 令牌第一次启动时随机生成，存在 var/demo/tokens.env（只有本用户可读）；控制台登录用其中的 RESCUE_SIM_CONTROL_TOKEN。
# DEEPSEEK_API_KEY、RAGFlow 的配置从 .env 读，只传给需要它的容器。演示数据都在 var/demo/，不混进开发的 var/（CLAUDE.md 约定 9）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/lib/env.sh && load_env .env
DEMO="$ROOT/var/demo"
COMPOSE=(docker compose -f docker/compose.yml --env-file "$DEMO/tokens.env")

tokens() {
  mkdir -p "$DEMO"
  if [ ! -f "$DEMO/tokens.env" ]; then
    umask 077
    {
      echo "RESCUE_MCP_MINE_DATA_TOKEN=$(openssl rand -hex 16)"
      echo "RESCUE_MCP_ACTIONS_TOKEN=$(openssl rand -hex 16)"
      echo "RESCUE_SIM_CONTROL_TOKEN=$(openssl rand -hex 16)"
    } >"$DEMO/tokens.env"
    echo "  已生成令牌 var/demo/tokens.env"
  fi
  mkdir -p "$DEMO/records" "$DEMO/runs" "$DEMO/ledger" "$DEMO/audit" "$DEMO/dsh-home" "$DEMO/documents"
}

wait_healthy() {
  local deadline=$((SECONDS + 600))
  for svc in mine-sim actions dsh; do
    while :; do
      id="$("${COMPOSE[@]}" ps -q "$svc")"
      st="$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo starting)"
      [ "$st" = healthy ] && { echo "  ✓ $svc"; break; }
      [ $SECONDS -ge $deadline ] && { echo "  ✗ $svc 未就绪（$st），查看：scripts/demo.sh logs" >&2; exit 1; }
      sleep 2
    done
  done
}

up() {
  : "${DEEPSEEK_API_KEY:?在 .env 里填写 DEEPSEEK_API_KEY}"
  export DEEPSEEK_API_KEY RAGFLOW_BASE_URL="${RAGFLOW_BASE_URL:-}" RAGFLOW_API_KEY="${RAGFLOW_API_KEY:-}" \
    KB_DATASETS_REGULATIONS="${KB_DATASETS_REGULATIONS:-}" KB_DATASETS_PLANS="${KB_DATASETS_PLANS:-}" KB_DATASETS_CASES="${KB_DATASETS_CASES:-}"
  tokens
  "${COMPOSE[@]}" up -d --build
  echo "== 等待健康检查"
  wait_healthy
  local url
  url="$("${COMPOSE[@]}" logs dsh 2>/dev/null | grep -Eo 'http://127\.0\.0\.1:[0-9]+/\?token=[A-Za-z0-9_-]+' | tail -1 || true)"
  echo
  echo "网页端：${url:-http://127.0.0.1:${DEMO_WEB_PORT:-13080}/（带 token 的地址见 scripts/demo.sh logs）}"
  echo "控制台：http://127.0.0.1:${DEMO_CONSOLE_PORT:-13090}/console（控制令牌见 var/demo/tokens.env 的 RESCUE_SIM_CONTROL_TOKEN）"
}

case "${1:-}" in
  up) up ;;
  down) [ -f "$DEMO/tokens.env" ] && "${COMPOSE[@]}" down || true ;;
  logs) "${COMPOSE[@]}" logs -f --tail 100 ;;
  reset)
    [ -f "$DEMO/tokens.env" ] && "${COMPOSE[@]}" down || true
    # 只清演示数据目录下这几项（不动令牌）；路径都在 $ROOT/var/demo 下
    for d in records runs ledger audit dsh-home documents; do
      case "$DEMO/$d" in "$ROOT"/var/demo/*) rm -rf -- "${DEMO:?}/$d" ;; esac
    done
    echo "  已清空 var/demo 的演示数据"
    up
    ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
