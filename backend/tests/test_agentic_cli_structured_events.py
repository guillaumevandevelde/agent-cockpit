"""Tests for the headless_run capability + ACP-isomorphic structured event model."""
import pytest

from app.services.agentic_cli.capabilities import (
    AGENTIC_CLI_CAPABILITY_MATRIX,
    CAPABILITY_KEYS,
    capability_flags,
    normalize_capability_matrix,
)
from app.services.agentic_cli.structured_events import (
    ErrorEvent,
    MessageChunkEvent,
    MessageRole,
    PermissionOption,
    PermissionOptionKind,
    PermissionRequestEvent,
    PlanEntry,
    PlanEntryPriority,
    PlanEntryStatus,
    PlanUpdateEvent,
    StructuredEventType,
    ToolCallEvent,
    ToolCallStatus,
    UsageResultEvent,
    parse_structured_event,
)


# --- capability matrix -------------------------------------------------------

def test_headless_run_is_a_capability_key():
    assert "headless_run" in CAPABILITY_KEYS


@pytest.mark.parametrize(
    "cli_id,expected_state,expected_flag",
    [
        ("claude-code", "supported", True),
        ("codex-cli", "supported", True),
        ("open-code", "supported", True),
        ("mimo-code", "unknown", False),
        ("copilot-cli", "unsupported", False),
    ],
)
def test_every_cli_classifies_headless_run(cli_id, expected_state, expected_flag):
    matrix = normalize_capability_matrix(cli_id)
    detail = matrix["headless_run"]
    assert detail["state"] == expected_state
    assert detail["reason"]  # every classification carries a rationale
    assert capability_flags(cli_id)["headless_run"] is expected_flag


def test_headless_run_declared_explicitly_for_all_known_clis():
    # No CLI is left to fall back on the normalize() "unknown" default —
    # each adapter explicitly declares whether/how it supports headless runs.
    for cli_id, matrix in AGENTIC_CLI_CAPABILITY_MATRIX.items():
        assert "headless_run" in matrix, cli_id


# --- ACP-isomorphic event model ---------------------------------------------

def test_event_type_enum_covers_the_six_acp_variants():
    assert {t.value for t in StructuredEventType} == {
        "message_chunk",
        "tool_call",
        "plan_update",
        "permission_request",
        "usage_result",
        "error",
    }


def test_message_chunk_roundtrip():
    event = MessageChunkEvent(session_id="s1", role=MessageRole.ASSISTANT, text="hi")
    parsed = parse_structured_event(event.model_dump(mode="json"))
    assert isinstance(parsed, MessageChunkEvent)
    assert parsed.text == "hi"
    assert parsed.role is MessageRole.ASSISTANT


def test_tool_call_carries_acp_raw_io_and_status():
    event = ToolCallEvent(
        tool_call_id="tc1",
        title="Read file",
        kind="read",
        status=ToolCallStatus.IN_PROGRESS,
        raw_input={"path": "a.py"},
        raw_output={"bytes": 12},
    )
    parsed = parse_structured_event(event.model_dump(mode="json"))
    assert isinstance(parsed, ToolCallEvent)
    assert parsed.tool_call_id == "tc1"
    assert parsed.raw_input == {"path": "a.py"}
    assert parsed.status is ToolCallStatus.IN_PROGRESS


def test_plan_update_entries():
    event = PlanUpdateEvent(
        entries=[
            PlanEntry(content="step 1", priority=PlanEntryPriority.HIGH, status=PlanEntryStatus.IN_PROGRESS),
            PlanEntry(content="step 2"),
        ]
    )
    parsed = parse_structured_event(event.model_dump(mode="json"))
    assert isinstance(parsed, PlanUpdateEvent)
    assert parsed.entries[0].status is PlanEntryStatus.IN_PROGRESS
    assert parsed.entries[1].priority is PlanEntryPriority.MEDIUM


def test_permission_request_options():
    event = PermissionRequestEvent(
        tool_call_id="tc1",
        title="Allow write?",
        options=[
            PermissionOption(option_id="o1", name="Allow", kind=PermissionOptionKind.ALLOW_ONCE),
            PermissionOption(option_id="o2", name="Reject", kind=PermissionOptionKind.REJECT_ONCE),
        ],
    )
    parsed = parse_structured_event(event.model_dump(mode="json"))
    assert isinstance(parsed, PermissionRequestEvent)
    assert parsed.options[0].kind is PermissionOptionKind.ALLOW_ONCE


def test_usage_result_fields():
    event = UsageResultEvent(stop_reason="end_turn", input_tokens=10, output_tokens=5, total_tokens=15, cost_usd=0.01)
    parsed = parse_structured_event(event.model_dump(mode="json"))
    assert isinstance(parsed, UsageResultEvent)
    assert parsed.stop_reason == "end_turn"
    assert parsed.total_tokens == 15


def test_error_event_uses_jsonrpc_shape():
    event = ErrorEvent(code=-32000, message="boom", data={"where": "spawn"})
    parsed = parse_structured_event(event.model_dump(mode="json"))
    assert isinstance(parsed, ErrorEvent)
    assert parsed.code == -32000
    assert parsed.message == "boom"


def test_discriminator_dispatches_by_type():
    parsed = parse_structured_event({"type": "tool_call", "tool_call_id": "x"})
    assert isinstance(parsed, ToolCallEvent)


def test_unknown_event_type_is_rejected():
    with pytest.raises(Exception):
        parse_structured_event({"type": "nonsense", "text": "x"})
