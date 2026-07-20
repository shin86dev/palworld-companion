from __future__ import annotations

from datetime import date
from typing import Any

from .models import CheckIn, PlanResult, Recommendation


class Planner:
    def __init__(self, bundle: dict[str, Any], today: date | None = None) -> None:
        self.bundle = bundle
        self.today = today or date.today()
        self.facts = {item["id"]: item for item in bundle["facts"]}
        self.locations = {item["id"]: item for item in bundle["locations"]}
        self.sources = {item["id"]: item for item in bundle["sources"]}

    def plan(self, check_in: CheckIn) -> PlanResult:
        if check_in.game_version != self.bundle["game_version"]:
            return PlanResult(None, (), "The local data bundle does not cover this game version.", ())
        candidates: list[Recommendation] = []
        matched_ids: list[str] = []
        topic_rules: list[dict[str, Any]] = []
        for rule in self.bundle["rules"]:
            if self._keywords_match(rule["when"], check_in):
                topic_rules.append(rule)
            if not self._matches(rule["when"], check_in):
                continue
            matched_ids.append(rule["id"])
            if not self._rule_is_eligible(rule):
                continue
            candidates.append(self._recommendation(rule))
        if not candidates:
            if matched_ids:
                reason = "A local rule matched, but its supporting facts are stale or not verified. No recommendation was made."
            elif topic_rules:
                minimum_level = min(rule["when"].get("min_level", 1) for rule in topic_rules)
                tiers = sorted({tier for rule in topic_rules for tier in rule["when"].get("tiers", [])})
                tier_text = ", ".join(tiers) if tiers else "any tier"
                reason = (
                    f"This topic is covered only from level {minimum_level} in the current audited rules "
                    f"({tier_text}). Level {check_in.level} / {check_in.tier} is outside that coverage, "
                    "so no recommendation was guessed."
                )
            else:
                reason = "This local bundle currently plans Ancient Civilization Cores and Pure Quartz. Choose one of those quick starts or use local search."
            return PlanResult(None, (), reason, tuple(matched_ids))
        candidates.sort(key=lambda item: (-item.score, item.rule_id))
        return PlanResult(candidates[0], tuple(candidates[1:3]), None, tuple(matched_ids))

    def search(self, query: str) -> list[dict[str, Any]]:
        normalized = " ".join(query.lower().split())
        terms = set(normalized.split())
        if not terms:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in [*self.bundle["facts"], *self.bundle["locations"]]:
            fields = [item.get("title", ""), item.get("name", ""), *item.get("aliases", [])]
            normalized_fields = [" ".join(field.lower().split()) for field in fields if field]
            searchable_terms = set(" ".join(normalized_fields).split())
            matched_terms = terms.intersection(searchable_terms)
            if not matched_terms or not self._item_is_current(item):
                continue
            score = len(matched_terms)
            if normalized in normalized_fields:
                score += 200
            elif any(len(field) >= 3 and (field in normalized or normalized in field) for field in normalized_fields):
                score += 100
            if terms.issubset(searchable_terms):
                score += 20
            scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        if scored and scored[0][0] >= 100:
            scored = [pair for pair in scored if pair[0] >= 100]
        return [item for _, item in scored]

    def _matches(self, when: dict[str, Any], check_in: CheckIn) -> bool:
        if check_in.level < when.get("min_level", 1) or check_in.level > when.get("max_level", 999):
            return False
        if tiers := when.get("tiers"):
            if check_in.tier not in tiers:
                return False
        return self._keywords_match(when, check_in)

    def _keywords_match(self, when: dict[str, Any], check_in: CheckIn) -> bool:
        text = f"{check_in.goal} {check_in.bottleneck}".lower()
        keywords = when.get("keywords", [])
        return not keywords or any(keyword.lower() in text for keyword in keywords)

    def _rule_is_eligible(self, rule: dict[str, Any]) -> bool:
        return all(self._item_is_current(self.facts[fact_id]) for fact_id in rule["fact_ids"])

    def _item_is_current(self, item: dict[str, Any]) -> bool:
        if item.get("confidence") != "verified":
            return False
        return (self.today - date.fromisoformat(item["last_checked"])).days <= 45

    def _recommendation(self, rule: dict[str, Any]) -> Recommendation:
        source_ids = tuple(dict.fromkeys(self.facts[fact_id]["source_id"] for fact_id in rule["fact_ids"]))
        return Recommendation(
            rule_id=rule["id"], title=rule["title"], action=rule["action"], rationale=rule["rationale"],
            prerequisites=tuple(rule.get("prerequisites", [])), destination_ids=tuple(rule["destination_ids"]),
            source_ids=source_ids, score=rule["priority"],
        )
