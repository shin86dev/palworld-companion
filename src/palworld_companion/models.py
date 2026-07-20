from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CheckIn:
    game_version: str
    level: int
    tier: str
    bottleneck: str = ""
    goal: str = ""
    completed_milestones: tuple[str, ...] = ()
    mounts: tuple[str, ...] = ()
    constraints: str = ""


@dataclass(frozen=True)
class Recommendation:
    rule_id: str
    title: str
    action: str
    rationale: str
    prerequisites: tuple[str, ...]
    destination_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    score: int


@dataclass(frozen=True)
class PlanResult:
    primary: Recommendation | None
    alternatives: tuple[Recommendation, ...]
    abstention_reason: str | None
    matched_rule_ids: tuple[str, ...]


class BundleError(ValueError):
    pass


def as_tuple(values: Any) -> tuple[str, ...]:
    return tuple(values or ())
