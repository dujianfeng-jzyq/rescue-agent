#!/usr/bin/env bash
# 一次性环境准备：Python 工作区、DSH bundle、rescue 配置档。
#
#   scripts/setup.sh            全部
#   scripts/setup.sh --no-dsh   只装 Python 部分（只做 MCP/模型开发的同事用这个）
#
# DSH 数据放在仓库内 .dsh-home/，不影响本机其他 DSH 配置档。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DSH_VERSION="0.2.1-rc.4"
WITH_DSH=1
[ "${1:-}" = "--no-dsh" ] && WITH_DSH=0

need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少 $1：$2" >&2; exit 1; }; }

echo "== Python 工作区"
need uv "https://docs.astral.sh/uv/ 安装"
uv sync --project mcp --all-packages
[ -f .env ] || { cp .env.example .env; sed -i "s#^RESCUE_HOME=.*#RESCUE_HOME=$ROOT#" .env; echo "  已生成 .env，请填写 DEEPSEEK_API_KEY"; }
mkdir -p var
uv run --project mcp python -m rescue_contract.export_schema >/dev/null && echo "  契约 JSON Schema 已导出到 mcp/contract/schema/"

[ $WITH_DSH = 1 ] || { echo "完成（未安装 DSH 部分）"; exit 0; }

echo "== DSH bundle"
need node "需要 Node.js 22+"
need pnpm "corepack enable && corepack prepare pnpm@latest --activate"
need dsh "npm i -g @deepseek-ai/dsh@$DSH_VERSION"
got="$(dsh --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?' | head -1 || true)"
[ "$got" = "$DSH_VERSION" ] || echo "  警告：dsh 版本是 ${got:-未知}，本项目锁定 $DSH_VERSION（插件 API 未稳定，升级需走验证清单）" >&2
(cd dsh-bundle && pnpm install && pnpm build)

echo "== rescue 配置档（DSH_HOME=$ROOT/.dsh-home）"
export DSH_HOME="$ROOT/.dsh-home"
export RESCUE_HOME="$ROOT"
# 从 web 模板创建（或重置）配置档，然后装入 bundle。重置会清掉已装插件，所以顺序不能反。
dsh rescue --from-default-profile web --dump-config >/dev/null
dsh plugin --profile rescue add ./dsh-bundle
echo "完成。下一步：scripts/dev.sh"
