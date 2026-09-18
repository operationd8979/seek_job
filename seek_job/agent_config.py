"""Load the model used by both dashboard agent phases."""
from __future__ import annotations

import re
from pathlib import Path

from .common import PipelineError, yaml_read

DEFAULT_AGENT_CONFIG = {"model": "gpt-5.6-sol", "reasoning_effort": "medium"}
REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def load_agent_config(root: Path) -> dict[str, str]:
    path = Path(root) / "config/agent-config.yaml"
    if not path.is_file():
        return DEFAULT_AGENT_CONFIG.copy()
    data = yaml_read(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or set(data) != {"model", "reasoning_effort"}:
        raise PipelineError("config/agent-config.yaml requires model and reasoning_effort only.")
    model = data["model"]
    effort = data["reasoning_effort"]
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}", model):
        raise PipelineError("config/agent-config.yaml: model must be a valid model ID.")
    if not isinstance(effort, str) or effort not in REASONING_EFFORTS:
        raise PipelineError("config/agent-config.yaml: reasoning_effort must be low, medium, high or xhigh.")
    return {"model": model, "reasoning_effort": effort}
