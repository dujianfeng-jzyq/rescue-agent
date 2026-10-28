# 由其他脚本 source：加载 .env，但已在 shell 里 export 的变量优先。
#
#   . "$ROOT/scripts/lib/env.sh"; load_env "$ROOT/.env"
#
# 直接 `set -a; . ./.env` 会让 .env 里的值（包括空值，如 `RESCUE_MCP_TOKEN=`）覆盖
# 命令行临时指定的值，`RESCUE_MCP_TOKEN=xxx scripts/mcp-up.sh` 就会静默失效。

load_env() {
  local file="${1:-.env}" key kv
  local -a keep=()
  [ -f "$file" ] || return 0
  # 先记下 .env 里出现、且当前已 export 的变量，加载后恢复
  for key in $(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$file"); do
    printenv "$key" >/dev/null && keep+=("$key=$(printenv "$key")")
  done
  set -a
  # shellcheck disable=SC1090
  . "$file"
  set +a
  for kv in "${keep[@]}"; do export "$kv"; done
}
