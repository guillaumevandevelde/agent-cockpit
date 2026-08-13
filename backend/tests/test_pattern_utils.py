"""Regression tests for ``app.utils.pattern_utils``.

Two scopes:
  1. Permission-pattern validation/migration (the original concern the
     module exists for — kept covered so a future edit doesn't quietly
     drop the existing guarantee).
  2. CC-2.1.224 settings surface (kanban card 9f90964e…). Policy on the
     card activity feed: ``show`` — enum-validate, never default. The
     tests below pin that contract across valid / invalid / absent / mixed
     inputs, including the integration through ``sanitize_permission_rules``
     and ``/api/v1/config/validate-settings`` (which the SPA's dry-run
     validator depends on).
"""
from app.utils.pattern_utils import (
    CROSS_SESSION_INBOUND_ALLOWED,
    DIALOG_EXPIRY_ALLOWED,
    sanitize_permission_rules,
    validate_permission_pattern,
)

# ---------------------------------------------------------------------------
# Allow-list sanity. If CC widens its enum, this is the first place to
# touch. The crossSessionInbound row in CC's settings doc (2.1.224+) and
# the dialogExpiry row are the upstream source of truth.
# ---------------------------------------------------------------------------


def test_cross_session_inbound_allowlist_matches_cc_2_1_224():
    # Strictness ladder `accept < hold < refuse` per CC docs/settings.
    assert frozenset({"accept", "hold", "refuse"}) == CROSS_SESSION_INBOUND_ALLOWED


def test_dialog_expiry_allowlist_matches_cc_2_1_224():
    # CC defaults to 5m; 60s/10m are documented alternatives; never
    # disables the deadline. See CC docs/settings#dialogexpiry.
    assert frozenset({"60s", "5m", "10m", "never"}) == DIALOG_EXPIRY_ALLOWED


# ---------------------------------------------------------------------------
# ``sanitize_permission_rules`` integration: every public-facing route
# (config_service.update_settings, api/v1/config.py:validate_settings)
# reaches this function, so testing through it covers the entire
# operator-facing surface.
# ---------------------------------------------------------------------------


def _removed_for(removed, key):
    """Return the `removed` entry whose `pattern` starts with `<key>=`,
    or None. Multiple `removed` entries on the same key would be a bug
    — assert that explicitly in callers that care."""
    matches = [r for r in removed if r["pattern"].startswith(f"{key}=")]
    assert len(matches) <= 1, f"duplicate removed-entry for {key}: {matches}"
    return matches[0] if matches else None


def test_show_valid_cross_session_inbound_passes_through():
    # `hold` is the explicit Cockpit recommendation for bypass-permissions
    # lanes (kanban card 9f90964e…, decision comment). Anything in the
    # allow-list must round-trip unchanged, including alongside permissions.
    settings = {
        "crossSessionInbound": "hold",
        "permissions": {"allow": ["Bash(ls)"]},
    }
    result = sanitize_permission_rules(settings)
    assert result["migrated"] == []
    assert result["removed"] == []
    assert result["sanitized_settings"]["crossSessionInbound"] == "hold"


def test_show_invalid_cross_session_inbound_is_dropped_with_reason():
    # `drop` is not in CC's enum; the show-policy drops the bad key and
    # records the reason so the Settings panel can surface a clear error.
    settings = {"crossSessionInbound": "drop"}
    result = sanitize_permission_rules(settings)
    assert "crossSessionInbound" not in result["sanitized_settings"]
    entry = _removed_for(result["removed"], "crossSessionInbound")
    assert entry is not None
    assert entry["category"] == "setting"
    assert "accept" in entry["reason"] and "refuse" in entry["reason"]
    assert "drop" in entry["reason"]


def test_show_absent_cross_session_inbound_unchanged():
    settings = {"permissions": {"allow": ["Bash(ls)"]}}
    result = sanitize_permission_rules(settings)
    assert "crossSessionInbound" not in result["sanitized_settings"]
    assert _removed_for(result["removed"], "crossSessionInbound") is None


def test_show_valid_dialog_expiry_passes_through():
    settings = {"dialogExpiry": "10m"}
    result = sanitize_permission_rules(settings)
    assert result["migrated"] == []
    assert result["removed"] == []
    assert result["sanitized_settings"]["dialogExpiry"] == "10m"


def test_show_invalid_dialog_expiry_is_dropped_with_reason():
    # `30m` is between CC's documented values; only 60s/5m/10m/never
    # are accepted. A typo here would otherwise pass through silently.
    settings = {"dialogExpiry": "30m"}
    result = sanitize_permission_rules(settings)
    assert "dialogExpiry" not in result["sanitized_settings"]
    entry = _removed_for(result["removed"], "dialogExpiry")
    assert entry is not None
    assert entry["category"] == "setting"
    assert "5m" in entry["reason"] and "never" in entry["reason"]
    assert "30m" in entry["reason"]


def test_show_invalid_30m_is_rejected():
    # Belt-and-braces against a future CC widening dialogExpiry to "30m";
    # the test fails the moment the allow-list is updated, prompting the
    # author to revisit the policy comment on the card.
    settings = {"dialogExpiry": "30m"}
    result = sanitize_permission_rules(settings)
    assert _removed_for(result["removed"], "dialogExpiry") is not None


def test_show_none_value_is_kept_silently():
    # `None` = "operator wants to remove the key but typed it out" — CC's
    # unset/default behaviour applies, so we don't drop it. The Settings
    # panel uses the same contract: an explicit `null` is a real signal.
    settings = {"crossSessionInbound": None, "dialogExpiry": None}
    result = sanitize_permission_rules(settings)
    assert result["sanitized_settings"]["crossSessionInbound"] is None
    assert result["sanitized_settings"]["dialogExpiry"] is None
    assert result["removed"] == []


def test_show_invalid_uppercase_variant_is_rejected():
    # CC's enums are case-sensitive per the docs ("accept" not "ACCEPT").
    # Match that strictly — silently accepting variants would mask typos.
    settings = {"crossSessionInbound": "ACCEPT"}
    result = sanitize_permission_rules(settings)
    assert _removed_for(result["removed"], "crossSessionInbound") is not None


def test_show_both_invalid_atomic_two_removed_entries():
    # When both keys are invalid, both must show up in `removed`. The
    # Settings panel renders one row per issue, so dropping either one
    # would leave the operator without feedback for that typo.
    settings = {"crossSessionInbound": "drop", "dialogExpiry": "30m"}
    result = sanitize_permission_rules(settings)
    assert _removed_for(result["removed"], "crossSessionInbound") is not None
    assert _removed_for(result["removed"], "dialogExpiry") is not None
    assert "crossSessionInbound" not in result["sanitized_settings"]
    assert "dialogExpiry" not in result["sanitized_settings"]


def test_show_one_invalid_one_valid_keeps_the_valid_one():
    # Atomicity is per-key, not per-call: a bad `dialogExpiry` must not
    # collateral-damage an otherwise-valid `crossSessionInbound`.
    settings = {"crossSessionInbound": "hold", "dialogExpiry": "30m"}
    result = sanitize_permission_rules(settings)
    assert result["sanitized_settings"]["crossSessionInbound"] == "hold"
    assert "dialogExpiry" not in result["sanitized_settings"]


def test_show_invalid_setting_does_not_break_permission_validation():
    # The integration point in `sanitize_permission_rules` runs the new
    # CC-2.1.224 validator first; a bad top-level setting must not also
    # silently skip the permission-pattern pass that follows. Use a
    # deprecated ``Bash(rm:*)`` form so the permission pass actually flags
    # it (a plain ``Bash(ls)`` would pass validation and we'd learn nothing;
    # a truly-bad pattern would land in `removed` instead of `migrated`).
    settings = {
        "crossSessionInbound": "drop",
        "permissions": {"allow": ["Bash(rm:*)"]},
    }
    result = sanitize_permission_rules(settings)
    assert _removed_for(result["removed"], "crossSessionInbound") is not None
    # The permission pass still ran: a deprecated pattern either migrates
    # (lands in `migrated`) or gets removed (lands in `removed`). Either is
    # proof the pass wasn't skipped; the bare counter is the point.
    assert len(result["migrated"]) + sum(
        1 for r in result["removed"] if r["category"] != "setting"
    ) >= 1, "permission validation silently skipped on bad CC setting"


# ---------------------------------------------------------------------------
# Sanity check that the legacy permission-pattern validation still works
# — protects against a future edit that accidentally tightens / breaks
# the original surface while adding the new keys.
# ---------------------------------------------------------------------------


def test_legacy_permission_validation_still_passes_valid_patterns():
    valid, error = validate_permission_pattern("Bash(ls)")
    assert valid is True and error is None


def test_legacy_permission_validation_still_flags_invalid_patterns():
    valid, error = validate_permission_pattern("Bash(rm:*)")
    assert valid is False
    assert error is not None
