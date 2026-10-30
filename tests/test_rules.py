from __future__ import annotations

from pathlib import Path

import pytest

from spacefinder.core.models import RiskLevel, RuleCategory, ScanRule
from spacefinder.core.rules import default_rules, rules_by_id


@pytest.fixture
def rules() -> tuple[ScanRule, ...]:
    home = Path("/home/tester")
    return default_rules(home, home / ".local" / "share")


def test_rule_ids_are_unique(rules) -> None:
    ids = [rule.id for rule in rules]
    assert len(ids) == len(set(ids))


def test_application_rules_are_opt_in_and_have_size_floor(rules) -> None:
    mapping = rules_by_id(rules)
    for rule_id in (
        "app_data",
        "dev_modules",
        "gradle_cache",
        "nuget_cache",
        "go_modules",
    ):
        rule = mapping[rule_id]
        assert rule.category is RuleCategory.APPS
        assert rule.default_selected is False
        assert rule.min_size_bytes > 0
        assert rule.requires_privilege is False


def test_large_cache_rule_covers_fresh_big_caches(rules) -> None:
    rule = rules_by_id(rules)["large_caches"]
    assert rule.category is RuleCategory.JUNK
    assert rule.default_selected is False
    assert rule.min_size_bytes >= 500 * 1024**2
    assert rule.min_age_days == 0


def test_system_apps_rule_only_shows_commands(rules) -> None:
    rule = rules_by_id(rules)["system_apps"]
    assert rule.category is RuleCategory.SYSTEM
    assert rule.requires_privilege is True
    assert rule.roots == ()
    assert any("pacman" in command for command in rule.info_commands)
    assert any("flatpak" in command for command in rule.info_commands)
    assert any("docker" in command for command in rule.info_commands)


def test_negative_size_floor_is_rejected() -> None:
    with pytest.raises(ValueError):
        ScanRule(
            id="bad",
            title="bad",
            description="bad",
            risk=RiskLevel.LOW,
            default_selected=False,
            roots=(Path("/tmp"),),
            min_size_bytes=-1,
        )
