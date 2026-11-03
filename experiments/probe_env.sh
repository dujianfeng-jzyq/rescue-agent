#!/usr/bin/env bash
# 实验 0001 第 0 步：环境探测（docs/experiments/0001-skill-vs-workflow.md 第 6 节）。
# 由 B 组总指挥在 DSH 的 bash 沙箱里运行，检查开放读写、执行后智能体能拿到什么、能改什么。
#
# 只检查，不泄露：输出会进入模型上下文（即发给模型服务商），所以
#   - 环境变量只列名字，不打印值；
#   - 令牌、密钥只统计出现次数，不输出内容；
#   - 网络探测只发 MCP initialize（不调用任何工具），只看 HTTP 状态码；
#   - 写入探测只新建一个空的隐藏文件并立即删除，不碰任何已有文件（台账、审计、快照）。
# 不要用 set -e：每项失败本身就是探测结果。
R="${1:?用法：probe_env.sh <仓库根目录>}"
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'

echo "== 0 基本信息"
echo "pwd=$(pwd)  uid=$(id -u)  shell=$0"

echo "== 1 环境变量（只列名字）"
env | cut -d= -f1 | grep -i -E 'token|key|secret|pass|cred|rescue|ragflow|deepseek|dsh' | sort | sed 's/^/  /'
echo "  共 $(env | wc -l) 个变量"

echo "== 2 令牌能否从文件或其他进程拿到（只计数）"
# .env 的值按 scripts/lib/env.sh 的方式解析（`RESCUE_MCP_TOKEN=   # 注释` 是空值，不能只看行首）
ENV_TOKEN=""
if [ -r "$R/.env" ]; then ENV_TOKEN="$(bash -c '. "$1/scripts/lib/env.sh"; unset RESCUE_MCP_TOKEN; load_env "$1/.env"; printf %s "${RESCUE_MCP_TOKEN:-}"' _ "$R" 2>/dev/null)"; fi
printf '  .env 可读：'; if [ -r "$R/.env" ]; then echo "是，解析出的 RESCUE_MCP_TOKEN 长度 ${#ENV_TOKEN}"; else echo 否; fi
printf '  .dsh-home/.credentials.yaml 可读：'; [ -r "$R/.dsh-home/.credentials.yaml" ] && echo 是 || echo 否
printf '  能读到环境里含 RESCUE_MCP_TOKEN 的其他进程数：'; grep -l -a 'RESCUE_MCP_TOKEN=' /proc/[0-9]*/environ 2>/dev/null | wc -l
printf '  能看到的进程总数：'; ls -d /proc/[0-9]* 2>/dev/null | wc -l

echo "== 3 网络（MCP initialize，不带令牌 / 带令牌）"
TOKEN_SRC=""
if [ -n "${RESCUE_MCP_TOKEN:-}" ]; then TOKEN_SRC=env; T="$RESCUE_MCP_TOKEN";
elif [ -n "$ENV_TOKEN" ]; then TOKEN_SRC=.env; T="$ENV_TOKEN"; fi
unset ENV_TOKEN
echo "  可用令牌来源：${TOKEN_SRC:-无}"
for p in 8101:mine_data 8105:kb 8106:actions 8107:workflow; do
  port=${p%%:*}; name=${p#*:}
  a=$(curl -s -o /dev/null -w '%{http_code}' -m 3 -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT" "http://127.0.0.1:$port/mcp" 2>/dev/null)
  b="-"
  if [ -n "$TOKEN_SRC" ]; then
    b=$(curl -s -o /dev/null -w '%{http_code}' -m 3 -X POST -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$INIT" "http://127.0.0.1:$port/mcp" 2>/dev/null)
  fi
  echo "  $name ($port)：不带令牌 ${a:-失败}，带令牌 $b"
done
unset T
printf '  外网（DeepSeek API）：'; curl -s -o /dev/null -w '%{http_code}\n' -m 5 https://api.deepseek.com/ 2>/dev/null || echo 失败

echo "== 4 写入（新建空隐藏文件后立即删除）"
for d in "$(pwd)" /tmp "$R" "$R/var" "$R/var/audit" "$R/var/eval/exp-0001" "$R/var/eval/exp-0001/snapshots" "$R/skills" "$R/experiments/skills" "$R/mcp" "$R/dsh-bundle" "$R/.dsh-home"; do
  f="$d/.probe-exp0001-$$"
  if [ ! -d "$d" ]; then echo "  $d：目录不存在"; continue; fi
  if touch "$f" 2>/dev/null; then echo "  $d：可写"; rm -f "$f"; else echo "  $d：不可写"; fi
done
printf '  已有脚本能否改写（只测打开写权限，不改内容）：'
if [ -w "$R/mcp/fire-model/src/rescue_fire_model/thresholds.yaml" ] && ( : >> "$R/mcp/fire-model/src/rescue_fire_model/thresholds.yaml" ) 2>/dev/null; then echo "thresholds.yaml 可写"; else echo "thresholds.yaml 不可写"; fi

echo "== 5 运行仓库里的 Python"
printf '  mcp/.venv/bin/python：'; "$R/mcp/.venv/bin/python" -c 'import rescue_fire_model, rescue_workflow; print("能导入 rescue_fire_model、rescue_workflow")' 2>&1 | tail -1
echo "== 结束"
