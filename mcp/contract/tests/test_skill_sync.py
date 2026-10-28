"""技能 red-line-rules 里的规则表必须覆盖规则引擎的全部规则，避免模型看到的说明与实际执行不一致。"""

from pathlib import Path

from rescue_contract.redlines import RULES

SKILL = Path(__file__).resolve().parents[3] / "skills" / "compliance" / "red-line-rules" / "SKILL.md"


def test_skill_lists_every_rule():
    text = SKILL.read_text(encoding="utf-8")
    for rule in RULES:
        assert rule.rule_id in text, f"red-line-rules 技能缺少 {rule.rule_id}"
        assert rule.article.split("、")[0] in text, f"red-line-rules 技能缺少 {rule.article}"
