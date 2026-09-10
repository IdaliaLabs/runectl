"""One explicit run state, no mixins (D6, plan §5.1).

The predecessor's coupling failure was ~30 instance attributes shared
implicitly across six mixins in the predecessor tool. Here there is exactly
one ``RunState``, owned by the loop; collaborators (:mod:`~runectl.tools.dispatch`,
:mod:`~runectl.flags.judge`, :mod:`~runectl.loop.context`) are plain objects
that take what they need and return values the loop applies — none of them
mutate ``RunState`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from runectl.categories.schema import Category
from runectl.flags.judge import ToolObservation
from runectl.providers.base import Message
from runectl.providers.registry import ModelInfo, ProviderName


class Challenge(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    category: str
    description: str
    files: tuple[Path, ...] = ()
    flag_format: str | None = None


@dataclass
class RunState:
    challenge: Challenge
    category: Category
    model: ModelInfo
    provider_name: ProviderName
    approval_policy: str
    max_steps: int

    history: list[Message] = field(default_factory=list)
    tool_observations: list[ToolObservation] = field(default_factory=list)

    step: int = 0
    cost_usd: float = 0.0
    # D16's primary metric: the progress *ratio*, not the step count. Fed by
    # `progress.tracker`, reported in `run.json`, and what `runectl bench`
    # optimizes toward.
    progress_steps: int = 0
    blocked_steps: int = 0

    flag: str | None = None
