# 常用命令入口。具体说明见 README.md。
.PHONY: setup setup-py dev up down smoke test test-py test-ts lint check eval schema sim-state clean

UV := uv run --project mcp

setup:            ## 全部环境（Python + DSH bundle + rescue 配置档）
	scripts/setup.sh
setup-py:         ## 只装 Python 部分
	scripts/setup.sh --no-dsh

dev:              ## 启动 MCP 服务 + DSH 网页端
	scripts/dev.sh
up:               ## 只启动 MCP 服务（含模拟矿井）
	scripts/mcp-up.sh
down:
	scripts/mcp-down.sh
smoke:            ## 对已启动的服务做冒烟测试
	$(UV) python scripts/smoke.py

test: test-py test-ts
test-py:
	cd mcp && uv run pytest -q
test-ts:
	cd dsh-bundle && pnpm test && pnpm typecheck
lint:
	cd mcp && uv run ruff check . ../eval ../scripts
check:            ## 提交前：lint + 全部测试 + patch 离线校验 + 基线评测
	$(MAKE) lint test
	python3 scripts/check_patches.py
	$(UV) python eval/baseline.py

eval:             ## 确定性基线评测（不需要大模型）
	$(UV) python eval/baseline.py
schema:           ## 重新导出契约 JSON Schema
	$(UV) python -m rescue_contract.export_schema
sim-state:
	python3 scripts/sim.py state

clean:
	rm -rf var/log var/run var/eval dsh-bundle/lib
