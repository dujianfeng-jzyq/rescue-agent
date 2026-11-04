# 常用命令入口。具体说明见 README.md。
.PHONY: setup setup-py dev dev-eval up down smoke test test-py test-ts lint check skills-check eval eval-agent schema sim-state clean demo-up demo-down demo-reset demo-logs demo-rehearse

UV := uv run --project mcp

setup:            ## 全部环境（Python + DSH bundle + rescue 配置档）
	scripts/setup.sh
setup-py:         ## 只装 Python 部分
	scripts/setup.sh --no-dsh

dev:              ## 启动 MCP 服务 + DSH 网页端
	scripts/dev.sh
dev-eval:         ## 智能体评测用：同 dev，但指令台账、研判快照、记录、值守事件和审计日志写到 var/eval/ 下（评测会放行指令）
	RESCUE_ACTIONS_LEDGER=$(CURDIR)/var/eval/actions-ledger.jsonl RESCUE_SNAPSHOT_DIR=$(CURDIR)/var/eval/snapshots RESCUE_MONITOR_DIR=$(CURDIR)/var/eval/monitor RESCUE_AUDIT_LOG=$(CURDIR)/var/eval/audit/tool-actions.jsonl RESCUE_RECORD_DIR=$(CURDIR)/var/eval/records scripts/dev.sh $(ARGS)
up:               ## 只启动 MCP 服务（含模拟矿井）
	scripts/mcp-up.sh
down:
	scripts/mcp-down.sh
demo-up:          ## 演示环境（Docker）：构建、启动、等健康检查，打印网页端和控制台地址
	scripts/demo.sh up
demo-down:
	scripts/demo.sh down
demo-reset:       ## 清空 var/demo 的演示数据并重新启动（模拟矿井回到常态）
	scripts/demo.sh reset
demo-logs:
	scripts/demo.sh logs
demo-rehearse:    ## 演示彩排：demo-reset 后按 docs/demo-script.md 自动走一遍（人工操作由脚本代做，会放行指令，只写 var/demo/）
	$(UV) --with playwright python eval/agent/rehearse.py --reset $(ARGS)
smoke:            ## 对已启动的服务做冒烟测试
	$(UV) python scripts/smoke.py

test: test-py test-ts
test-py:
	cd mcp && uv run pytest -q
test-ts:
	cd dsh-bundle && pnpm test && pnpm typecheck
lint:
	cd mcp && uv run ruff check . ../eval ../scripts ../skills
check:            ## 提交前：lint + 全部测试 + 技能自检 + patch 离线校验 + 基线评测
	$(MAKE) lint test skills-check
	python3 scripts/check_patches.py
	$(UV) python eval/baseline.py

skills-check:     ## 第二版技能自检：结构、import、全部样例（skills/README.md）
	$(UV) python -m rescue_contract.skills check

eval:             ## 确定性基线评测（不需要大模型）
	$(UV) python eval/baseline.py
eval-agent:       ## 智能体端到端评测（需已启动 DSH 网页端；ARGS="--url … --model … --repeat 3"）
	$(UV) --with playwright python eval/agent/run.py $(ARGS)
schema:           ## 重新导出契约 JSON Schema
	$(UV) python -m rescue_contract.export_schema
sim-state:
	python3 scripts/sim.py state

clean:
	rm -rf var/log var/run var/eval dsh-bundle/lib
