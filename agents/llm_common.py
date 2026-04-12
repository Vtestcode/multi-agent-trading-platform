from __future__ import annotations

import json
import os
from typing import Any


def resolve_model_name(model_name: str | None = None) -> str:
    resolved = model_name or os.getenv("PYDANTIC_AI_MODEL")
    if not resolved:
        raise ValueError(
            "PYDANTIC_AI_MODEL is required. For example, set PYDANTIC_AI_MODEL to an OpenAI model string such as "
            "'openai:gpt-4.1-mini'."
        )
    return resolved


def compact_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)
