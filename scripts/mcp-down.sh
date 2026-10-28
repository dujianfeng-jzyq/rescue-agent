#!/usr/bin/env bash
# 停止 scripts/mcp-up.sh 启动的 MCP 服务。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
shopt -s nullglob
for pidfile in var/run/*.pid; do
  key="$(basename "$pidfile" .pid)"
  pid="$(cat "$pidfile")"
  if kill "$pid" 2>/dev/null; then echo "  停止 $key（PID $pid）"; fi
  rm -f "$pidfile"
done
