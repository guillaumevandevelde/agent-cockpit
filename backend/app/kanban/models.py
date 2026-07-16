"""Kanban ORM models. Two layers:
- KanbanOp: append-only operation log (source of truth + activity feed).
- KanbanCard / KanbanDeliverable: materialized, derived state for fast reads.
- KanbanMeta: small key/value store (device_id, per-project flags).
- KanbanPlan: first-class plans scoped to a project_key. Replaces the legacy
  file-backed PlanService (see docs/cockpit/00-orientation.md §3-bomen-regel).
  Pure CRUD table — no KanbanOp log because the JSONL-based plan/session
  linking (get_plan_sessions) keeps working independently.
"""
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.kanban.db import KanbanBase


def _utcnow() -> datetime:
    return datetime.now(UTC)


class KanbanOp(KanbanBase):
    __tablename__ = "kanban_ops"

    op_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # "<device>:<seq>"
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    hlc: Mapped[str] = mapped_column(String(48), index=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))   # card | comment | deliverable
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    op_type: Mapped[str] = mapped_column(String(16))       # create|move|update|claim|release|comment|attach
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KanbanCard(KanbanBase):
    __tablename__ = "kanban_cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    column: Mapped[str] = mapped_column(String(32), default="Backlog")
    rank: Mapped[str] = mapped_column(String(64), default="")
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Structured routing hint (analysis | feature | bug | chore). Distinct from
    # `labels`: an enum is unambiguous to map to a persona; free-form labels are
    # not. See docs/cockpit/work-type-routing-analysis.md §2A.
    work_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Explicit model override for the spawned session (e.g. "opus", "sonnet",
    # a full model id). Precedence: card.model > column.default_model >
    # persona frontmatter `model:` > no --model flag (platform default). See
    # docs/superpowers/specs/2026-07-10-kanban-model-override-design.md.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Per-agent-column (persona) model+provider override, shape:
    #   { "<column-name>": {"model": str|null, "provider": str|null} }
    # At dispatch, once the phase's target column/persona is resolved, its
    # entry (if any) wins over both the column's default_provider and the
    # card/column/persona model precedence — see dispatch._effective_model.
    # Same free-text, no-enum contract as `model`/`labels`; nullable, so
    # existing rows round-trip as null (backwards compatible). Covers analyst
    # and executor phases alike because both resolve their target column here.
    column_overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    transport: Mapped[str | None] = mapped_column(String(16), nullable=True)  # worktree | sandcastle | auto (null)
    resume_session_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    resume_project_folder: Mapped[str | None] = mapped_column(String(512), nullable=True)
    scheduled_at: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ISO8601; auto-dispatch ignores the card until this time
    # Per-dispatch telemetry breadcrumbs written by dispatch._run_card after a
    # successful spawn. Together they let the per-card token telemetry
    # endpoint (services.dispatch_usage_service.get_card_usage) attribute
    # the spawned session's JSONL transcript to this card. Nullable so
    # existing rows (legacy cards dispatched before this feature) round-trip
    # unchanged. See kanban card 8a2ad986.
    #
    # dispatch_started_at is stored as an ISO8601 *string* (mirrors
    # `scheduled_at`) because the op-log payload goes through SQLite's
    # JSON column — datetime objects don't round-trip. The service layer
    # parses it with `_ensure_aware`.
    dispatch_started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dispatch_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatch_project_folder: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dispatch_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analyst_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    executor_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_card_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    analyst_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depends_on: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Free-form key/value bag for integration data (external IDs, workflow
    # provenance, sha of last-seen upstream commit, …). Mirrors claude-task-master's
    # task.metadata: callers can attach integration-specific data without a schema
    # migration. JSON-serialized like labels/depends_on; nullable, defaults to None
    # so existing rows continue to round-trip as null.
    # Python attribute is `meta` because SQLAlchemy's Declarative API reserves
    # `metadata` on the base class. The DB column stays `metadata` (set via the
    # positional first arg to mapped_column) so the API contract and DB schema
    # both use the name documented in the kanban card; pydantic schemas below
    # bind to this attribute via Field(alias="metadata").
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    # Consecutive dispatch-target failures (spawn died within seconds, or raised
    # synchronously before any session existed). Reset once a dispatch runs long
    # enough to prove the target works, or when the streak trips the Impediment
    # move. See dispatch.MAX_DISPATCH_FAILURES / dispatch._release_dead_claim.
    dispatch_failures: Mapped[int] = mapped_column(Integer, default=0)
    claimed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Per-field HLCs powering last-write-wins. DORMANT with one device: these guards
    # never reject a live write (every new tick dominates) and only do work under
    # HLC-ordered replay. Kept as cheap multi-device insurance, not removed.
    # See docs/cockpit/sync-hlc-freeze-vs-prune.md.
    title_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    description_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    column_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    rank_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    claim_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)

    deliverables: Mapped[list["KanbanDeliverable"]] = relationship(
        back_populates="card", cascade="all, delete-orphan",
    )


class KanbanDeliverable(KanbanBase):
    __tablename__ = "kanban_deliverables"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("kanban_cards.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))   # pr|branch|commit|link|note|spec
    ref: Mapped[str] = mapped_column(Text)          # portable reference, never a local path
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    card: Mapped["KanbanCard"] = relationship(back_populates="deliverables")


class KanbanColumn(KanbanBase):
    __tablename__ = "kanban_columns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    name: Mapped[str] = mapped_column(String(128))
    rank: Mapped[str] = mapped_column(String(64), default="")
    default_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Which subscription/vendor cards dispatched into this column spawn against
    # (anthropic | bedrock | minimax, see provider_env.py). None means the
    # dispatcher's own default (anthropic). Orthogonal to default_agent, which
    # picks the CLI/persona, not the backend the CLI authenticates against.
    default_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Column-wide model default, one precedence level below a card's own
    # `model` and above the persona frontmatter default. Same free-text,
    # no-validation contract as default_agent/default_provider.
    default_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Per-column session cap. NULL means no per-column limit — falls back to the
    # project-level max_sessions cap. Set to e.g. 1 so only one card at a time
    # dispatches into this column.
    max_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KanbanMeta(KanbanBase):
    __tablename__ = "kanban_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class KanbanGate(KanbanBase):
    """A decision gate on a card: an open question + structured choices that a
    human answers from the Kanban UI while the agent session blocks and waits
    (via the `open_gate` MCP tool), instead of ending the session the way
    report_impediment does.
    """
    __tablename__ = "kanban_gates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("kanban_cards.id"), index=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KanbanWorkTypeMapping(KanbanBase):
    """Per-project override of the work_type → persona routing.

    Each row maps a single work_type (analysis | feature | bug | chore) to a
    persona (typically the stem of a .claude/agents/<persona>.md file) for one
    project. Missing rows fall back to the default mapping
    (`WORK_TYPE_PERSONA_DEFAULTS` in schemas.py). Used at create_card time to
    auto-fill `card.agent` from `card.work_type` — see dispatch work_type
    follow-ups.
    """
    __tablename__ = "kanban_work_type_mappings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    work_type: Mapped[str] = mapped_column(String(32))
    persona: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KanbanPlan(KanbanBase):
    """First-class plan scoped to a project_key.

    Replaces the legacy file-backed ``PlanService`` (see kanban card
    727470a8 / docs/cockpit/00-orientation.md §3 *drie-bomen-regel*).
    One row per (project_key, slug); uniqueness on the pair keeps the
    migration script idempotent and the API's ``{filename}`` URL
    unambiguous.

    Distinct from ``KanbanDeliverable(kind='plan')`` — those are analyst
    plan-attachments *on a card* (parent + child-card dependency wiring).
    ``KanbanPlan`` is the project-level plan library: a project-scoped
    replacement for ``~/.claude/plans/*.md``.
    """
    __tablename__ = "kanban_plans"
    __table_args__ = (
        UniqueConstraint(
            "project_key", "slug", name="uq_kanban_plans_project_slug",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    slug: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    # Mirrors KanbanCard.meta — reserved for integration data (spec-doc
    # links, workflow provenance, …). Nullable so existing rows round-trip
    # as null. Defaults to None so the API contract never returns the
    # key back as an explicit null.
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
