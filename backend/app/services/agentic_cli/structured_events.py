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
| ``rate_limit``            | *(none — deliberate ACP super-set; see below)*     |
| ``session_init``          | *(none — deliberate ACP super-set; see below)*     |
| ``context_usage``         | ``session/update`` → ``usage_update`` (mid-turn)   |

The middle two (``rate_limit``, ``session_init``) are **deliberately outside ACP's
``session/update`` vocabulary** — they're a conscious super-set of ACP, not an
oversight. ACP has no quota/rate-limit notification (it's a CLI-side concern,
not a transport concern) and no session-init notification either (ACP's
counterpart is the ``session/new`` *response*, not a ``session/update``).
``claude -p --output-format stream-json`` emits both, including the
``rate_limit`` event that justifies the whole headless transport
(`docs/cockpit/headless-stream-json-transport-spike.md` §4.1). A future
ACP-backed transport is allowed to leave them empty without that being a bug
— they exist here so the first (Claude) transport has somewhere to put them.

The last one (``context_usage``) is an ACP-isomorphic variant for the
``session/update`` → ``usage_update`` *mid-turn* notification (measured against
OpenCode 1.18.8 in ``docs/cockpit/acp-transport-opencode-go-nogo.md`` §4).
It is NOT a super-set like ``rate_limit`` and ``session_init`` — it is the
ACP-native counterpart for a real ACP event, included here so a future
ACP-backed transport has a first-class variant to emit it under. The
existing claude-code stream-json mapper does not emit one (Claude's native
event is terminal usage, mapped to ``usage_result``), and that is not a bug.

Every event carries an optional ``session_id`` so a multiplexed transport can
attribute events to the originating headless run.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class StructuredEventType(StrEnum):
    """The event kinds a headless run may emit.

    The first six are ACP-isomorphic; ``context_usage`` is the ACP counterpart
    for the mid-turn ``usage_update`` notification; ``rate_limit`` and
    ``session_init`` are documented super-set additions (see module docstring).
    """

    MESSAGE_CHUNK = "message_chunk"
    TOOL_CALL = "tool_call"
    PLAN_UPDATE = "plan_update"
    PERMISSION_REQUEST = "permission_request"
    USAGE_RESULT = "usage_result"
    ERROR = "error"
    RATE_LIMIT = "rate_limit"
    SESSION_INIT = "session_init"
    CONTEXT_USAGE = "context_usage"


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


class RateLimitStatus(StrEnum):
    """The ``status`` field of a Claude ``rate_limit_event``.

    ``allowed_warning`` is the operationally interesting one: the request was
    *allowed*, but utilisation crossed a threshold — so a consumer that
    observes it can pause *before* the eventual 429, instead of scraping pane
    text for the rejection after the fact.
    """

    ALLOWED = "allowed"
    ALLOWED_WARNING = "allowed_warning"
    REJECTED = "rejected"


class RateLimitType(StrEnum):
    """The window the rate-limit event applies to."""

    FIVE_HOUR = "five_hour"
    SEVEN_DAY = "seven_day"
    SEVEN_DAY_OVERAGE = "seven_day_opus"
    MONTHLY = "monthly"


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


class RateLimitEvent(_StructuredEventBase):
    """A rate-limit / quota notification from the CLI.

    **Deliberate ACP super-set.** ACP has no concept of rate-limit / quota
    notifications — quota is a CLI-side concern, not a transport concern, so
    ``session/update`` never carries one. But Claude's ``stream-json`` output
    emits a typed ``rate_limit_event`` mid-run with the very fields that make
    the headless transport worth shipping
    (``status: allowed_warning`` at ``utilization: 0.97`` *before* the
    eventual 429 — see
    ``docs/cockpit/headless-stream-json-transport-spike.md`` §4.1(a) / §6.1).

    Wrapping it as ``error`` would be wrong (``allowed_warning`` ≠ error);
    wrapping it as ``usage_result`` would also be wrong (that's terminal, this
    isn't). The fix is to extend the schema as a *documented* super-set of
    ACP: a future ACP-backed transport is allowed to never emit one, without
    that being a bug.

    Note that ``status`` is the one field the original CLI always sets, so we
    keep it required; everything else is best-effort and may be absent in
    payloads from future CLI versions.
    """

    type: Literal[StructuredEventType.RATE_LIMIT] = StructuredEventType.RATE_LIMIT
    status: RateLimitStatus
    resets_at: int | None = None
    rate_limit_type: RateLimitType | None = None
    utilization: float | None = None
    is_using_overage: bool | None = None
    surpassed_threshold: float | None = None


class SessionInitEvent(_StructuredEventBase):
    """The CLI's first event: it has started, here's its session handle.

    **Deliberate ACP super-set.** ACP's counterpart is the ``session/new``
    *response*, not a ``session/update`` notification — so this event has no
    place in the ACP-isomorphic core. But ``claude -p
    --output-format stream-json`` emits a ``system/init`` as its very first
    line, carrying exactly the readiness + session-handle signal the headless
    transport needs (it replaces the box-drawing scrape that
    ``wait_for_pane_ready`` does today). Mapping it as ``message_chunk`` would
    lie about its semantics; mapping it as ``usage_result`` would lie even
    worse. The fix is the same as ``rate_limit``: extend the schema as a
    documented super-set of ACP and let a future ACP adapter never emit one.

    ``session_id`` is required (it *is* the readiness signal — if it's
    missing, the payload is incomplete, not just partial). The other fields
    are what ``stream-json`` happens to set today; future CLI versions may add
    more, which we tolerate by accepting the payload without them.
    """

    type: Literal[StructuredEventType.SESSION_INIT] = StructuredEventType.SESSION_INIT
    # NB: this event's primary payload field is *also* named session_id — that
    # is intentional (it matches Claude's stream-json field name) and distinct
    # from the multiplexed-transport session_id inherited from the base.
    # Both are preserved on the model; pydantic's default behaviour keeps both
    # accessible as separate attributes.
    session_id: str = Field(...)  # type: ignore[assignment]
    cwd: str | None = None
    model: str | None = None
    permission_mode: str | None = None


class ContextUsageCost(BaseModel):
    """The ``cost`` sub-object carried by an ACP ``usage_update`` notification.

    ACP vendors (e.g. OpenCode 1.18.8) emit ``{"amount": <number>,
    "currency": "USD"}`` as the cost block; the snake_case translation in this
    model preserves the structure (no flattening into ``cost_usd``) so a
    future change in either field is a one-line edit, not a round of
    migration across consumers.
    """

    amount: float
    currency: str


class ContextUsageEvent(_StructuredEventBase):
    """A mid-turn context-window signal (ACP ``session/update`` →
    ``usage_update``).

    This is the ACP-native counterpart for an ACP event the model did not
    previously carry (``docs/cockpit/acp-transport-opencode-go-nogo.md`` §4).
    It is intentionally distinct from :class:`UsageResultEvent` — that one
    maps the *terminal* ``session/prompt`` result (``stopReason`` + usage),
    while this one carries a *mid-turn* notification about how full the
    context window is. The two read from the same wire field but mean
    different things, so they get different variants.

    Like :class:`RateLimitEvent`, the operational use is to pause **before**
    you hit a limit instead of scraping for an error after the fact. The
    measured payload from OpenCode 1.18.8 was
    ``{"used":29108,"size":200000,"cost":{"amount":0,"currency":"USD"}}``;
    ``cost`` is optional because not every ACP vendor includes the cost
    block — absence is not a malformed payload.

    The existing claude-code stream-json transport does not emit this
    variant (Claude's stream-json has no equivalent mid-turn usage signal,
    only a terminal usage block). That is not a bug.
    """

    type: Literal[StructuredEventType.CONTEXT_USAGE] = StructuredEventType.CONTEXT_USAGE
    used: int
    size: int
    cost: ContextUsageCost | None = None


StructuredEvent = Annotated[
    MessageChunkEvent
    | ToolCallEvent
    | PlanUpdateEvent
    | PermissionRequestEvent
    | UsageResultEvent
    | ErrorEvent
    | RateLimitEvent
    | SessionInitEvent
    | ContextUsageEvent,
    Field(discriminator="type"),
]

STRUCTURED_EVENT_ADAPTER: TypeAdapter[StructuredEvent] = TypeAdapter(StructuredEvent)


def parse_structured_event(payload: dict[str, Any]) -> StructuredEvent:
    """Validate a raw payload into the matching structured-event model.

    Dispatches on the ``type`` discriminator; raises ``pydantic.ValidationError``
    for an unknown type or a malformed payload.
    """
    return STRUCTURED_EVENT_ADAPTER.validate_python(payload)
