"""红线规则说明（plan-compliance 技能；第一版的 red-line-rules 草稿 P4 移除）必须覆盖规则引擎的全部规则，避免模型看到的说明与实际执行不一致。"""

from pathlib import Path

import pytest
from rescue_contract.redlines import RULES

SKILLS = Path(__file__).resolve().parents[3] / "skills"


@pytest.mark.parametrize("skill", ["plan-compliance"])
def test_skill_lists_every_rule(skill):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    for rule in RULES:
        assert rule.rule_id in text, f"{skill} 技能缺少 {rule.rule_id}"
        assert rule.article.split("、")[0] in text, f"{skill} 技能缺少 {rule.article}"
