"""第二版技能之间复制的辅助模块必须相同（技能自成一体、只能 import 本目录的模块，见 skills/README.md）。"""

from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[3] / "skills"
# 辅助模块 → 以哪个技能的为准、复制到哪些技能
SHARED = {
    "_quality.py": ("fire-assessment", ["explosion-triangle", "sealing-plan", "effect-review"]),
    "_evidence.py": ("fire-assessment", ["explosion-triangle", "sealing-plan", "effect-review"]),
    "_skill.py": ("fire-assessment", ["explosion-triangle", "emergency-escalation", "sealing-plan", "plan-compliance", "effect-review"]),
}


@pytest.mark.parametrize("module", sorted(SHARED))
def test_shared_helpers_identical(module):
    source, copies = SHARED[module]
    want = (SKILLS / source / "scripts" / module).read_bytes()
    for skill in copies:
        assert (SKILLS / skill / "scripts" / module).read_bytes() == want, f"{skill}/scripts/{module} 与 {source} 的不同，两份要一起改"
