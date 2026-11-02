"""工作流的取数规则（workflow.yaml）。"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PATH = Path(os.environ.get("RESCUE_WORKFLOW_CONFIG") or Path(__file__).with_name("workflow.yaml"))


@cache
def load(path: Path = PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
