from datetime import date, timedelta

from palworld_companion.models import CheckIn
from palworld_companion.planner import Planner
from test_bundle import bundle


def check_in(**overrides):
    defaults = dict(game_version="1.0", level=70, tier="endgame", bottleneck="oil", goal="", mounts=())
    defaults.update(overrides)
    return CheckIn(**defaults)


def test_matching_rule_returns_primary():
    result = Planner(bundle(), today=date(2026, 7, 13)).plan(check_in())
    assert result.primary.rule_id == "r"
    assert result.abstention_reason is None


def test_unknown_version_abstains():
    result = Planner(bundle(), today=date(2026, 7, 13)).plan(check_in(game_version="0.7"))
    assert result.primary is None
    assert "does not cover" in result.abstention_reason


def test_stale_fact_abstains():
    payload = bundle()
    payload["facts"][0]["last_checked"] = (date(2026, 7, 13) - timedelta(days=46)).isoformat()
    result = Planner(payload, today=date(2026, 7, 13)).plan(check_in())
    assert result.primary is None
    assert "verified" in result.abstention_reason


def test_local_keyword_search_and_no_result():
    planner = Planner(bundle(), today=date(2026, 7, 13))
    assert planner.search("Node")[0]["id"] == "l"
    assert planner.search("unmapped") == []
