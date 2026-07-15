"""ACP-isomorphic structured event model for headless CLI runs.

This is the internal event schema emitted by a headless (``headless_run``)
structured-event transport — the first instance being ``claude -p
--output-format stream-json`` (see ``docs/cockpit/acp-transport-decision.md``
§6 kaart 3). The model is deliberately **ACP-isomorphic**: its variants mirror
the Agent-Client Protocol ``session/update`` notifications plus the
``session/request_permission`` request and the ``session/prompt`` result, so a
later ACP-backed transport can reuse this exact schema instead of inventing a
second event vocabulary.

ACP uses ``camelCase`` JSON keys; this model uses ``snake_case`` (the
Cockpit/Python convention). The *structure and semantics* are isomorphic — the
casing is the only translation a future ACP adapter performs. The mapping:

| this model                | ACP counterpart                                    |
|---------------------------|----------------------------------------------------|
| ``message_chunk``         | ``session/update`` → ``agent_message_chunk`` /     |
|                           | ``user_message_chunk`` / ``agent_thought_chunk``   |
| ``tool_call``             | ``session/update`` → ``tool_call`` /               |
|                           | ``tool_call_update``                               |
| ``plan_update``           | ``session/update`` → ``plan``                      |
| ``permission_request``    | ``session/request_permission`` (request)           |
| ``usage_result``          | ``session/prompt`` result (``stopReason``) + usage |
| ``error``                 | JSON-RPC 2.0 error object                          |

Every event carries an optional ``session_id`` so a multiplexed transport can
attribute events to the originating headless run.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class StructuredEventType(StrEnum):
    """The six ACP-isomorphic event kinds a headless run may emit."""

    MESSAGE_CHUNK = "message_chunk"
    TOOL_CALL = "tool_call"
    PLAN_UPDATE = "plan_update"
    PERMISSION_REQUEST = "permission_request"
    USAGE_RESULT = "usage_result"
    ERROR = "error"


class MessageRole(StrEnum):
    """Author of a message chunk (mirrors ACP's message-chunk variants)."""

    ASSISTANT = "assistant"
    USER = "user"
    THOUGHT = "thought"


class ToolCallStatus(StrEnum):
    """Lifecycle of a tool call (mirrors ACP ``ToolCallStatus``)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanEntryStatus(StrEnum):
    """Status of a single plan entry (mirrors ACP ``PlanEntryStatus``)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PlanEntryPriority(StrEnum):
    """Priority of a single plan entry (mirrors ACP ``PlanEntryPriority``)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PermissionOptionKind(StrEnum):
    """Permission option semantics (mirrors ACP ``PermissionOptionKind``)."""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    REJECT_ONCE = "reject_once"
    REJECT_ALWAYS = "reject_always"


class _StructuredEventBase(BaseModel):
    """Fields shared by every structured event."""

    session_id: str | None = None


class MessageChunkEvent(_StructuredEventBase):
    """A streamed message fragment (ACP ``*_message_chunk``)."""

    type: Literal[StructuredEventType.MESSAGE_CHUNK] = StructuredEventType.MESSAGE_CHUNK
    role: MessageRole = MessageRole.ASSISTANT
    text: str


class ToolCallEvent(_StructuredEventBase):
    """A tool call or its incremental update (ACP ``tool_call``)."""

    type: Literal[StructuredEventType.TOOL_CALL] = StructuredEventType.TOOL_CALL
    tool_call_id: str
    title: str | None = None
    kind: str | None = None
    status: ToolCallStatus = ToolCallStatus.PENDING
    raw_input: dict[str, Any] | None = None
    raw_output: dict[str, Any] | None = None


class PlanEntry(BaseModel):
    """One entry in a plan update (ACP ``PlanEntry``)."""

    content: str
    priority: PlanEntryPriority = PlanEntryPriority.MEDIUM
    status: PlanEntryStatus = PlanEntryStatus.PENDING


class PlanUpdateEvent(_StructuredEventBase):
    """The agent's current plan (ACP ``plan``)."""

    type: Literal[StructuredEventType.PLAN_UPDATE] = StructuredEventType.PLAN_UPDATE
    entries: list[PlanEntry] = Field(default_factory=list)


class PermissionOption(BaseModel):
    """A selectable answer to a permission request (ACP ``PermissionOption``)."""

    option_id: str
    name: str
    kind: PermissionOptionKind | None = None


class PermissionRequestEvent(_StructuredEventBase):
    """A gating request for tool authorization (ACP ``session/request_permission``)."""

    type: Literal[StructuredEventType.PERMISSION_REQUEST] = StructuredEventType.PERMISSION_REQUEST
    tool_call_id: str | None = None
    title: str | None = None
    options: list[PermissionOption] = Field(default_factory=list)


class UsageResultEvent(_StructuredEventBase):
    """Terminal usage/result for a prompt turn (ACP ``session/prompt`` result)."""

    type: Literal[StructuredEventType.USAGE_RESULT] = StructuredEventType.USAGE_RESULT
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


class ErrorEvent(_StructuredEventBase):
    """A transport or agent error (JSON-RPC 2.0 error object shape)."""

    type: Literal[StructuredEventType.ERROR] = StructuredEventType.ERROR
    code: int | None = None
    message: str
    data: dict[str, Any] | None = None


StructuredEvent = Annotated[
    MessageChunkEvent | ToolCallEvent | PlanUpdateEvent | PermissionRequestEvent | UsageResultEvent | ErrorEvent,
    Field(discriminator="type"),
]

STRUCTURED_EVENT_ADAPTER: TypeAdapter[StructuredEvent] = TypeAdapter(StructuredEvent)


def parse_structured_event(payload: dict[str, Any]) -> StructuredEvent:
    """Validate a raw payload into the matching structured-event model.

    Dispatches on the ``type`` discriminator; raises ``pydantic.ValidationError``
    for an unknown type or a malformed payload.
    """
    return STRUCTURED_EVENT_ADAPTER.validate_python(payload)
