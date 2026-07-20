from datetime import date

from palworld_companion.bundle import load_bundle
from palworld_companion.models import CheckIn
from palworld_companion.planner import Planner


def test_level_70_core_bottleneck_returns_sourced_mapped_plan():
    planner = Planner(load_bundle(), today=date(2026, 7, 13))

    result = planner.plan(
        CheckIn(
            game_version="1.0",
            level=70,
            tier="endgame",
            bottleneck="Ancient Civilization Cores",
            mounts=("Frostallion",),
        )
    )

    assert result.abstention_reason is None
    assert result.primary is not None
    assert "core" in result.primary.title.lower()
    assert result.primary.source_ids
    assert result.primary.destination_ids


def test_local_search_understands_acc_shorthand():
    planner = Planner(load_bundle(), today=date(2026, 7, 13))

    results = planner.search("best ACC farm")

    assert results
    assert results[0]["id"] == "ancient-core-expedition-yields"
    assert all("quartz" not in item["id"] and "bark" not in item["id"] for item in results)


def test_quartz_bottleneck_prefers_automation_with_expedition_bootstrap():
    planner = Planner(load_bundle(), today=date(2026, 7, 13))

    result = planner.plan(
        CheckIn(
            game_version="1.0",
            level=70,
            tier="endgame",
            bottleneck="Pure Quartz",
        )
    )

    assert result.abstention_reason is None
    assert result.primary is not None
    assert result.primary.rule_id == "quartz-quarry-automation"
    assert any(item.rule_id == "quartz-expedition-bootstrap" for item in result.alternatives)


def test_recurring_bottleneck_aliases_have_honest_local_scope_notes():
    planner = Planner(load_bundle(), today=date(2026, 7, 13))

    cases = {
        "how do I farm ATP": "ancient-technology-points-scope",
        "retire oil extractors": "crude-oil-scope",
        "farm ancient bark": "ancient-bark-scope",
    }

    for query, expected_id in cases.items():
        assert expected_id in {item["id"] for item in planner.search(query)}


def test_bundle_contains_pinned_licensed_fast_travel_catalog():
    payload = load_bundle()
    nodes = [item for item in payload["locations"] if item["kind"] == "fast_travel"]

    assert len(nodes) == 171
    assert len({item["id"] for item in nodes}) == 171

    midpoint = next(item for item in nodes if item["name"] == "Mount Obsidian - Midpoint")
    assert (midpoint["x"], midpoint["y"]) == (-498, -444)
    assert midpoint["coordinate_system"] == "palpagos-map-v1"

    world_tree = next(item for item in nodes if item["name"] == "The Verdant Rootpath")
    assert world_tree["coordinate_status"] == "verified"
    assert world_tree["coordinate_system"] == "world-tree-map-v1"
    assert -2500 <= world_tree["x"] <= 2500
    assert -2500 <= world_tree["y"] <= 2500

    source = next(item for item in payload["sources"] if item["id"] == "palworld-save-tools-fast-travel-1.0")
    assert source["license"] == "MIT"
    assert source["pinned_revision"] == "e4e1439b274c1140eed5690051ce59ab14b68027"


def test_bundle_integrates_map_reveal_watchtowers_as_higher_tier_waypoints():
    payload = load_bundle()
    watchtowers = [
        item for item in payload["locations"]
        if item.get("waypoint_class") == "watchtower"
    ]

    assert len(watchtowers) == 22
    windswept = next(item for item in watchtowers if item["name"] == "Windswept Island Watchtower")
    assert windswept["kind"] == "fast_travel"
    assert windswept["reveals_map"] is True
    assert windswept["upstream_id"] == "WatchTower_1"
    assert windswept["coordinate_status"] == "verified"


def test_bundle_contains_current_alpha_pals_as_always_available_pois():
    payload = load_bundle()
    alpha_pals = [item for item in payload["locations"] if item["kind"] == "alpha_pal"]

    assert len(alpha_pals) == 90
    assert len({item["id"] for item in alpha_pals}) == 90
    assert sum(item["region"] == "palpagos" for item in alpha_pals) == 82
    assert sum(item["region"] == "world-tree" for item in alpha_pals) == 8
    assert all("unlocked" not in item for item in alpha_pals)

    anubis = next(item for item in alpha_pals if item["name"] == "Anubis")
    assert anubis["pal_id"] == "Anubis"
    assert anubis["level_min"] == anubis["level_max"] == 55
    assert anubis["coordinate_status"] == "verified"
    assert anubis["region"] == "palpagos"
    assert anubis["upstream_id"] == "alpha-46-Anubis"

    source = next(item for item in payload["sources"] if item["id"] == "palworld-atlas-alpha-pals-24088465")
    assert source["license"] == "MIT"
    assert source["pinned_revision"] == "1063da140f38cc80791007ab7d440fe8e121466b"


def test_low_level_covered_topic_explains_boundary_instead_of_generic_failure():
    result = Planner(load_bundle(), today=date(2026, 7, 13)).plan(
        CheckIn(game_version="1.0", level=20, tier="early", bottleneck="Ancient Civilization Cores")
    )

    assert result.primary is None
    assert "level 55" in result.abstention_reason
    assert "no recommendation was guessed" in result.abstention_reason


def test_level_73_plan_does_not_describe_user_as_level_70():
    result = Planner(load_bundle(), today=date(2026, 7, 13)).plan(
        CheckIn(game_version="1.0", level=73, tier="endgame", bottleneck="Ancient Civilization Cores")
    )

    assert result.primary is not None
    assert "At level 70" not in result.primary.action
