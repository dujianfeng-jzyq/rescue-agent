#!/usr/bin/env bash
# 第二版的沙箱探测（实验 0001 第 0 步 probe_env.sh 的第二版）：P0 验收“第 0 步探测在新预设下复跑结果不变差”和验证清单 #21。
# 由第二版总指挥在 bash 沙箱里运行。只检查，不泄露：环境变量只列名字，令牌只计数，网络只发 MCP initialize 和 GET /sim/state 看状态码，
# 写入只新建空的隐藏文件并立即删除，不碰已有文件。
R="${1:?用法：probe_v2.sh <仓库根目录> [记录目录]}"
REC="${2:-$R/var/records}"
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'

echo "== 0 基本信息"
echo "pwd=$(pwd)  uid=$(id -u)  容器：$([ -f /.dockerenv ] && echo 是 || echo 否)"
echo "== 1 环境变量（只列名字）"
env | cut -d= -f1 | grep -i -E 'token|key|secret|pass|cred|rescue|ragflow|deepseek|dsh' | sort | sed 's/^/  /'
echo "  共 $(env | wc -l) 个变量"
echo "== 2 能否从文件或其他进程拿到令牌（只计数）"
printf '  .env 可读：'; [ -r "$R/.env" ] && echo 是 || echo 否
printf '  DSH 凭据文件可读：'; ls "$R"/.dsh-home/*/.credentials.yaml "$R"/.dsh-home/.credentials.yaml 2>/dev/null | while read -r f; do [ -r "$f" ] && printf '%s ' "${f#$R/}"; done; echo
for v in RESCUE_MCP_TOKEN RESCUE_MCP_MINE_DATA_TOKEN RESCUE_MCP_ACTIONS_TOKEN RESCUE_SIM_CONTROL_TOKEN DEEPSEEK_API_KEY; do
  printf '  环境里含 %s 的其他进程数：' "$v"; grep -l -a "$v=" /proc/[0-9]*/environ 2>/dev/null | wc -l
done
printf '  能看到的进程总数：'; ls -d /proc/[0-9]* 2>/dev/null | wc -l
echo "== 3 网络（不带令牌）"
for p in 8101:mine_data 8106:actions; do
  port=${p%%:*}; name=${p#*:}
  a=$(curl -s -o /dev/null -w '%{http_code}' -m 3 -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT" "http://127.0.0.1:$port/mcp" 2>/dev/null)
  echo "  $name MCP：${a:-失败}"
done
printf '  mine_data /sim/state：'; curl -s -o /dev/null -w '%{http_code}\n' -m 3 http://127.0.0.1:8101/sim/state 2>/dev/null || echo 失败
printf '  外网（DeepSeek API）：'; curl -s -o /dev/null -w '%{http_code}\n' -m 5 https://api.deepseek.com/ 2>/dev/null || echo 失败
echo "== 4 写入（新建空隐藏文件后立即删除）"
for d in "$(pwd)" /tmp "$R" "$R/var" "$REC" "$R/var/audit" "$R/skills" "$R/mcp" "$R/dsh-bundle" "$R/profiles" "$R/.dsh-home"; do
  f="$d/.probe-v2-$$"
  if [ ! -d "$d" ]; then echo "  ${d#$R/}：目录不存在"; continue; fi
  if touch "$f" 2>/dev/null; then echo "  ${d}：可写"; rm -f "$f"; else echo "  ${d}：不可写"; fi
done
echo "== 结束"
