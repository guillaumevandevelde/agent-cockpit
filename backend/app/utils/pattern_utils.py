"""Permission pattern validation and migration utilities.

Validates permission patterns against Claude Code's current rules and
provides migration for deprecated pattern formats.
"""
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Maximum length for a permission pattern
MAX_PATTERN_LENGTH = 500

# Regex for valid tool names (including MCP tool names like mcp__server__tool)
TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Regex for Tool(argument) format
TOOL_ARG_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\((.+)\)$", re.DOTALL)

# Regex for Tool:subcommand format (only :* at the end)
TOOL_SUBCOMMAND_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\*$")

# Regex to detect deprecated :* inside Tool(...) arguments
DEPRECATED_COLON_STAR_RE = re.compile(r":\*$")

# CC settings Cockpit surfaces + enum-validates (kanban card 9f90964e…).
# Policy: show (validate enum, no default) — see the comment on the card
# activity feed for the reasoning. Values pulled from the CC settings docs
# (Claude Code 2.1.224+); widen the frozensets upstream-side-by-side with CC.
# `crossSessionInbound`: strictness ladder `accept < hold < refuse` per
# https://code.claude.com/docs/en/settings (`crossSessionInbound` row).
CROSS_SESSION_INBOUND_ALLOWED: frozenset[str] = frozenset({
    "accept",
    "hold",
    "refuse",
})
# `dialogExpiry`: deadline for held-message approval dialogs (and other
# remote-client dialogs). CC reads it from user/managed/--settings sources
# only — Cockpit-side project/local writes are silently ignored by CC, but
# we still validate them so an operator typo doesn't pass the gate silently.
DIALOG_EXPIRY_ALLOWED: frozenset[str] = frozenset({
    "60s",
    "5m",
    "10m",
    "never",
})


def validate_permission_pattern(pattern: str) -> tuple[bool, str | None]:
    """
    Validate a permission pattern against Claude Code's current rules.

    Args:
        pattern: The permission pattern string to validate.

    Returns:
        Tuple of (is_valid, error_message). error_message is None if valid.
    """
    if not pattern or not pattern.strip():
        return False, "Pattern must not be empty"

    if "\n" in pattern or "\r" in pattern:
        return False, "Pattern must not contain newline characters"

    if len(pattern) > MAX_PATTERN_LENGTH:
        return False, f"Pattern exceeds maximum length of {MAX_PATTERN_LENGTH} characters"

    # Check Tool(argument) format
    match = TOOL_ARG_RE.match(pattern)
    if match:
        tool = match.group(1)
        arg = match.group(2)
        # Check for deprecated :* inside parentheses (but not for MCP patterns
        # where server:* is the standard syntax for "all tools from server")
        if tool != "MCP" and DEPRECATED_COLON_STAR_RE.search(arg):
            return False, (
                "The :* pattern inside Tool(...) is deprecated. "
                "Use space-wildcard instead: e.g., Bash(command *) not Bash(command:*)"
            )
        return True, None

    # Check Tool:* format (valid — prefix matching at tool level)
    if TOOL_SUBCOMMAND_RE.match(pattern):
        return True, None

    # Check simple tool name (e.g., "Bash", "WebSearch", "mcp__server__tool")
    if TOOL_NAME_RE.match(pattern):
        return True, None

    return False, f"Invalid pattern format: {pattern}"


def migrate_deprecated_pattern(pattern: str) -> str | None:
    """
    Attempt to migrate a deprecated pattern to the current valid format.

    Args:
        pattern: The deprecated permission pattern.

    Returns:
        The migrated pattern string, or None if migration is not possible.
    """
    # Can't migrate patterns with newlines (multiline commands)
    if "\n" in pattern or "\r" in pattern:
        return None

    # Can't migrate patterns that are too long
    if len(pattern) > MAX_PATTERN_LENGTH:
        return None

    # Migrate Tool(arg:*) -> Tool(arg *)
    match = TOOL_ARG_RE.match(pattern)
    if match:
        tool = match.group(1)
        arg = match.group(2)
        # Don't migrate MCP patterns — server:* is valid MCP syntax
        if tool != "MCP" and DEPRECATED_COLON_STAR_RE.search(arg):
            migrated_arg = DEPRECATED_COLON_STAR_RE.sub(" *", arg)
            return f"{tool}({migrated_arg})"

    return None


def _validate_cc_known_settings(
    settings: dict[str, Any],
    removed: list[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Enum-validate the top-level CC settings Cockpit surfaces (kanban
    card 9f90964e…, CC 2.1.224 surface). Policy: ``show`` — accept every
    value in the allow-list, drop everything else with a reason attached.
    ``None`` values are kept (treated as "operator wants to remove the
    key but didn't type it out"); CC's own unset/default behaviour applies.

    Returns ``(sanitized_settings, removed)`` so callers can extend the same
    ``removed`` accumulator they pass to ``sanitize_permission_rules``.
    """
    sanitized = settings
    for key, allowed in (
        ("crossSessionInbound", CROSS_SESSION_INBOUND_ALLOWED),
        ("dialogExpiry", DIALOG_EXPIRY_ALLOWED),
    ):
        if not isinstance(sanitized, dict) or key not in sanitized:
            continue
        value = sanitized[key]
        if value is None or value in allowed:
            continue
        allowed_sorted = sorted(allowed)
        removed.append({
            "pattern": f"{key}={value!r}",
            "category": "setting",
            "reason": (
                f"{key} must be one of {allowed_sorted} "
                f"(Claude Code 2.1.224+); got {value!r}"
            ),
        })
        sanitized = {**sanitized}
        del sanitized[key]
    return sanitized, removed


def sanitize_permission_rules(settings: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and sanitize all permission patterns in a settings dict.

    Auto-migrates deprecated patterns and removes invalid ones.
    Returns a dict with:
      - migrated: list of {original, migrated, category}
      - removed: list of {pattern, category, reason}
      - sanitized_settings: the cleaned settings dict

    Args:
        settings: The settings dictionary potentially containing
                  permissions.allow, permissions.ask, permissions.deny
                  plus top-level ``crossSessionInbound`` / ``dialogExpiry``
                  (kanban card 9f90964e… — CC 2.1.224 surface).

    Returns:
        Dict with migrated, removed, and sanitized_settings keys.
    """
    migrated: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []

    # Run the CC-2.1.224 known-settings validator first so an unknown value
    # never reaches permission validation with a stale setting alongside it.
    sanitized_settings, removed = _validate_cc_known_settings(
        settings, removed
    )

    permissions = sanitized_settings.get("permissions")
    if not isinstance(permissions, dict):
        return {
            "migrated": migrated,
            "removed": removed,
            "sanitized_settings": sanitized_settings,
        }

    # Re-bind the now-pruned permissions dict onto a fresh top-level copy.
    sanitized_settings = {
        **sanitized_settings,
        "permissions": {**permissions},
    }

    for category in ("allow", "ask", "deny"):
        rules = permissions.get(category)
        if not isinstance(rules, list):
            continue

        clean_rules: list[str] = []
        for pattern in rules:
            if not isinstance(pattern, str):
                removed.append({
                    "pattern": str(pattern),
                    "category": category,
                    "reason": "Pattern is not a string",
                })
                continue

            is_valid, error = validate_permission_pattern(pattern)
            if is_valid:
                clean_rules.append(pattern)
                continue

            # Try to migrate
            migrated_pattern = migrate_deprecated_pattern(pattern)
            if migrated_pattern is not None:
                is_valid_migrated, _ = validate_permission_pattern(migrated_pattern)
                if is_valid_migrated:
                    clean_rules.append(migrated_pattern)
                    migrated.append({
                        "original": pattern,
                        "migrated": migrated_pattern,
                        "category": category,
                    })
                    logger.info(
                        "Migrated permission pattern: %s -> %s",
                        pattern, migrated_pattern,
                    )
                    continue

            # Can't migrate — remove
            removed.append({
                "pattern": pattern,
                "category": category,
                "reason": error or "Invalid pattern",
            })
            logger.warning(
                "Removed invalid permission pattern from %s: %s (%s)",
                category, pattern, error,
            )

        sanitized_settings["permissions"][category] = clean_rules

    return {
        "migrated": migrated,
        "removed": removed,
        "sanitized_settings": sanitized_settings,
    }
