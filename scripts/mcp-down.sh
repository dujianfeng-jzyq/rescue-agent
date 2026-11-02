#!/usr/bin/env bash
# 停止 scripts/mcp-up.sh 启动的 MCP 服务。
#
#   scripts/mcp-down.sh            停止全部
#   scripts/mcp-down.sh mine_data  只停止指定服务（与 mcp-up.sh 的参数一致）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
shopt -s nullglob
if [ "$#" -gt 0 ]; then
  pidfiles=()
  for key in "$@"; do pidfiles+=("var/run/$key.pid"); done
else
  pidfiles=(var/run/*.pid)
fi
for pidfile in "${pidfiles[@]}"; do
  [ -f "$pidfile" ] || continue
  key="$(basename "$pidfile" .pid)"
  pid="$(cat "$pidfile")"
  if kill "$pid" 2>/dev/null; then echo "  停止 $key（PID $pid）"; fi
  rm -f "$pidfile"
done
