"""REST API for the kanban board. All mutations go through apply_operation."""
import json
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.config import settings
from app.kanban import attachments as attachment_store
from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanAttachment, KanbanCard
from app.kanban.operations import ClaimRejected, apply_operation, release_card_claim
from app.kanban.project_key import resolve_project_key, resolve_project_path
from app.kanban.schemas import (
    WORK_TYPES,
    ActiveSubscriptionOverrideRequest,
    ActivityEntry,
    AddPlanAttachmentRequest,
    AddPlanAttachmentResponse,
    AgentStatsResponse,
    AttachRequest,
    AutodispatchRequest,
    CardCreate,
    CardResponse,
    CardSummaryResponse,
    CardUpdate,
    ClaimRequest,
    ColumnClearRequest,
    ColumnCreate,
    ColumnResponse,
    ColumnUpdate,
    CommentRequest,
    CreateProjectFromIntakeRequest,
    CreateProjectFromIntakeResponse,
    DefaultTransportRequest,
    DispatchRequest,
    EnableRequest,
    GateAnswerRequest,
    GateOpenRequest,
    GateResponse,
    ImpedimentResolveRequest,
    MoveRequest,
    RedispatchRequest,
    ReopenRequest,
    ReorderRequest,
    ReviewRequest,
    RunLedger,
    SetGateRequest,
    ShipModeRequest,
    SkipPermissionsRequest,
    SubscriptionPoolRequest,
    TakeOverRequest,
    UpdatePlanAttachmentRequest,
    WachtrijItem,
    WachtrijResponse,
    WorkTypeMappingBulk,
    WorkTypeMappingResponse,
)
from app.kanban.schemas import (
    CardUsageModelBreakdown as _ModelBreakdownResponse,
)
from app.kanban.schemas import (
    CardUsageResponse as _CardUsageResponse,
)
from app.utils.url_utils import resolve_base_url

logger = logging.getLogger(__name__)

# Fallback routing for an impediment when no target_agent is given. Only the
# two agents that actually exist in .claude/agents/ are listed; the first
# entry is chosen. Vestigial roles from the old card-flow.json
# (developer / tester / testing / code-review) are removed — see
# docs/cockpit/work-type-routing-analysis.md §5.3.
_IMPEDIMENT_AGENTS = {
    "analyst": ["engineer"],
    "engineer": ["analyst"],
}


def _write_json_atomic(target: Path, data: dict) -> None:
    """Write JSON via a temp file + os.replace so a crash mid-write can't
    corrupt an existing .mcp.json."""
    import os
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, target)


def _unknown_project_key_http_error(
    project_key: str, known: set[str], *, for_create: bool
) -> HTTPException:
    """Build a 404 for an unknown `project_key` on `GET /cards` / `POST /cards`.

    Mirrors the MCP-side ``_unknown_project_key_error`` payload shape
    (``backend/app/kanban/mcp_server.py:90``) so any REST fallback recipe
    written off the documented ``-32602`` MCP-failure instructions can branch
    on the same ``error`` key. Field name is ``project_key`` here (matching
    the REST query/body field) instead of MCP's ``project`` — keeps each
    layer's wire format consistent with its own schemas.

    The hint tells callers about the two legitimate paths: (1) call
    ``POST /kanban/enable`` first to onboard (the normal flow — the resulting
    columns put the key into ``known_project_keys``), or (2) re-post with
    ``confirm_new_project=True`` for the rare case where a script knows it's
    creating the very first card of a brand-new project. 404 is consistent
    with how this router already reports ``"card not found"`` /
    ``"column not found"``.
    """
    sample = sorted(known)[:10]
    if for_create:
        hint = (
            "Call POST /api/v1/kanban/enable first to onboard this project "
            "(which seeds the columns and makes the key known), or pass "
            "confirm_new_project=true to deliberately create its first card."
        )
    else:
        hint = (
            "Call POST /api/v1/kanban/enable first to onboard this project, "
            "or use the resolved key from GET /api/v1/kanban/project-key."
        )
    return HTTPException(
        status_code=404,
        detail={
            "error": "unknown_project_key",
            "project_key": project_key,
            "message": (
                f"No existing cards or columns found for project key "
                f"{project_key!r} — this is likely a typo or a guessed key. "
                f"{hint}"
            ),
            "known_project_keys_sample": sample,
        },
    )


async def _assert_project_key_known(s, project_key: str, *, for_create: bool) -> None:
    """Raise 404 ``unknown_project_key`` if ``project_key`` has no cards or columns.

    Reuses ``service.known_project_keys`` (the single source of truth shared
    with the MCP tools) — see kanban card 91c85199 for the incident this
    guards against and `docs/cockpit/kanban-conventions.md` for the broader
    contract. The check sits at the top of every REST handler that takes a
    ``project_key`` so a typo / guessed key from a dispatched agent (e.g.
    via the documented MCP-``-32602`` REST fallback) can't silently return
    an empty list or create an orphaned card.
    """
    known = await service.known_project_keys(s)
    if project_key not in known:
        raise _unknown_project_key_http_error(
            project_key, known, for_create=for_create
        )


router = APIRouter(prefix="/kanban", tags=["Kanban"])


@router.get("/columns")
async def columns(project_key: str = Query(...)):
    async with KanbanSessionLocal() as s:
        cols = await service.list_columns(s, project_key)
        return {"columns": [ColumnResponse.model_validate(c) for c in cols]}


@router.post("/columns", response_model=ColumnResponse, status_code=status.HTTP_201_CREATED)
async def create_column(payload: ColumnCreate):
    async with KanbanSessionLocal() as s:
        try:
            col = await service.create_column(
                s, project_key=payload.project_key, name=payload.name,
                rank=payload.rank, default_agent=payload.default_agent,
                default_provider=payload.default_provider,
                default_model=payload.default_model,
                max_sessions=payload.max_sessions,
            )
        except ValueError as e:
            # kaart 293d1faa…: unknown ``default_provider`` surfaces as 422
            # so the operator sees the rejection at config time (matches the
            # active-subscription-override and subscription-pool handlers).
            raise HTTPException(422, str(e))
        await s.commit()
        return ColumnResponse.model_validate(col)


@router.patch("/columns/{column_id}", response_model=ColumnResponse)
async def update_column(column_id: str, payload: ColumnUpdate):
    # exclude_unset=True distinguishes "field absent from the PATCH" from
    # "field present and explicitly set to null" — the column-pause UI's ∞
    # button sends {max_sessions: null} to clear an existing cap, and a future
    # patch might want to wipe default_agent / default_provider / default_model
    # to None. Without exclude_unset, the silent drop in service.update_column
    # would make every "clear X" a no-op (the same gap that hid behind the
    # provider→cli rename in kaart ad15e0827… — don't reintroduce it).
    patch = payload.model_dump(exclude_unset=True)
    async with KanbanSessionLocal() as s:
        # (provider, model) co-validation: a column with default_provider=minimax
        # and default_model=opus would silently fall through to opus at dispatch
        # time — the "stuck on opus" report behind kaart 1782fa43…. Reject the
        # mismatch with 422 so the inconsistency can't land (kaart 1782fa43…
        # follow-up). Validation is skipped when either side is null (the
        # dispatch chain re-validates the resolved combo at spawn time) or when
        # the provider has no model-options cache (e.g. bedrock).
        if "default_provider" in patch or "default_model" in patch:
            existing = await service.get_column(s, column_id)
            if existing is None:
                raise HTTPException(404, "column not found")
            new_provider = patch.get("default_provider", existing.default_provider)
            new_model = patch.get("default_model", existing.default_model)
            if new_provider and new_model:
                allowed = await _allowed_models_for_provider(s, new_provider)
                if allowed is not None and new_model not in allowed:
                    raise HTTPException(
                        422,
                        f"model {new_model!r} is not valid for provider "
                        f"{new_provider!r}; known options: {allowed}",
                    )
        try:
            col = await service.update_column(s, column_id, **patch)
        except ValueError as e:
            # kaart 293d1faa…: same fail-fast as ``create_column`` — an
            # unknown provider in a PATCH body is rejected with 422.
            raise HTTPException(422, str(e))
        if col is None:
            raise HTTPException(404, "column not found")
        await s.commit()
        return ColumnResponse.model_validate(col)


async def _allowed_models_for_provider(session, provider: str) -> list[str] | None:
    """Return the list of models the column-update validator accepts for a
    given provider, or ``None`` when the provider has no model-options
    cache and free-form model strings are allowed.

    Provider→cache mapping mirrors ``dispatch.get_cached_model_options``
    (claude-code aliases — sonnet/opus/haiku/…) and
    ``dispatch.get_cached_minimax_model_options`` (discovered-from-JSONL).
    Bedrock has no cache (AWS model ids are ARN-shaped, not the bare aliases
    the cli returns); ``None`` means "no validation, accept any string".
    """
    from app.kanban import dispatch
    if provider == "minimax":
        return await dispatch.get_cached_minimax_model_options(session)
    if provider == "anthropic":
        return await dispatch.get_cached_model_options(session)
    return None


@router.delete("/columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_column(column_id: str):
    async with KanbanSessionLocal() as s:
        if not await service.delete_column(s, column_id):
            raise HTTPException(404, "column not found")
        await s.commit()


@router.get("/model-options")
async def model_options():
    """Cached list of Claude model aliases (sonnet/opus/haiku/...), refreshed
    on demand via POST .../model-options/refresh. Seed defaults until the
    first refresh."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"provider": "claude-code",
                "options": await dispatch.get_cached_model_options(s)}


@router.post("/model-options/refresh")
async def refresh_model_options():
    """Re-query the installed `claude` CLI for its current model alias list
    and cache it. 502 if the CLI isn't installed/reachable -- the cached list
    from the last successful refresh (or the seed) is left untouched."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            options = await dispatch.refresh_claude_model_options(s)
        except (OSError, subprocess.SubprocessError) as e:
            raise HTTPException(502, f"failed to query claude CLI: {e}") from e
        await s.commit()
        return {"provider": "claude-code", "options": options}


@router.get("/model-options/minimax")
async def minimax_model_options():
    """Cached list of MiniMax model ids (discovered from JSONL usage logs).

    Discovery path is sync ``glob ~/.claude/projects/**/*.jsonl`` and
    filter-by-prefix — there is no `claude -p "/model"` equivalent for
    MiniMax (the subscription speaks Anthropic-compatible, not the
    model-picker CLI). Seed defaults to ``MiniMax-M3`` until the first
    refresh; refresh is best-effort and 502s only on hard filesystem
    errors (the cached list / seed is left untouched otherwise).
    """
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {
            "provider": "minimax",
            "options": await dispatch.get_cached_minimax_model_options(s),
        }


@router.post("/model-options/minimax/refresh")
async def refresh_minimax_model_options():
    """Re-scan JSONL logs for unique MiniMax model ids and cache the list.

    Returns the freshly discovered list (which may equal the seed on a
    machine that has never dispatched a MiniMax session). The cache is
    only overwritten when the scan yields a non-empty list, mirroring the
    claude-code refresh path's "don't clobber good cache with empty
    result" guard.
    """
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            options = await dispatch.refresh_minimax_model_options(s)
        except OSError as e:
            raise HTTPException(502, f"failed to scan minimax JSONL: {e}") from e
        await s.commit()
        return {"provider": "minimax", "options": options}


@router.get("/columns/{column_id}/effective-model")
async def column_effective_model(column_id: str):
    """Resolved provider/model/source for a column (UI precedence display).

    Walks the same chain dispatch.py uses (board-wide override → pool →
    per-card column_override → column.default_model → persona) and
    returns ``provider``, ``model``, and a ``provider_source`` /
    ``model_source`` label naming which level actually won. The
    column-settings dialog renders this beneath the model input so the
    user sees *why* a board-wide override or pool choice silently wins
    over their selection (kaart 1782fa43…).
    """
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        column = await service.get_column(s, column_id)
        if column is None:
            raise HTTPException(404, "column not found")
        project_path = await resolve_project_path(column.project_key)
        info = await dispatch.resolve_column_effective_model(
            s, project_key=column.project_key, column_name=column.name,
            project_path=project_path or "",
        )
    return info


# Work-type → persona routing (per-project overrides; falls back to
# WORK_TYPE_PERSONA_DEFAULTS for any work_type without a row).
# See docs/cockpit/work-type-routing-analysis.md §5.5.


@router.get("/work-type-mappings")
async def list_work_type_mappings(project_key: str = Query(...)):
    """Return the *complete* per-project {work_type: persona} map: stored
    overrides merged on top of `WORK_TYPE_PERSONA_DEFAULTS`. The response
    always contains an entry for every work_type in `WORK_TYPES`, so the UI
    can render the form without special-casing missing rows.
    """
    async with KanbanSessionLocal() as s:
        merged = await service.work_type_mapping_for_project(s, project_key)
        return {"project_key": project_key, "mappings": merged}


@router.post("/work-type-mappings/bulk", response_model=list[WorkTypeMappingResponse])
async def bulk_upsert_work_type_mappings(payload: WorkTypeMappingBulk):
    """Bulk-replace the per-project mapping. Missing work_types fall back to
    the default — they are *not* deleted by this call. Returns the full set
    of stored rows for the project (overrides only, not the merged view).
    """
    for m in payload.mappings:
        if m.work_type not in WORK_TYPES:
            raise HTTPException(422, f"work_type must be one of {WORK_TYPES}")
    async with KanbanSessionLocal() as s:
        rows = await service.bulk_replace_work_type_mappings(
            s, project_key=payload.project_key,
            mappings=[m.model_dump() for m in payload.mappings],
        )
        await s.commit()
        return [WorkTypeMappingResponse.model_validate(r) for r in rows]


@router.delete("/work-type-mappings/{work_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_type_mapping(work_type: str, project_key: str = Query(...)):
    """Remove the override for a single (project_key, work_type) pair. Idempotent:
    deleting a non-existent row returns 204 as well so the UI can call it
    without checking first. The next `GET` will show the default again.
    """
    if work_type not in WORK_TYPES:
        raise HTTPException(422, f"work_type must be one of {WORK_TYPES}")
    async with KanbanSessionLocal() as s:
        await service.delete_work_type_mapping(s, project_key, work_type)
        await s.commit()


@router.get("/stats", response_model=AgentStatsResponse)
async def agent_stats(project_key: str = Query(...)):
    """Per-agent performance: time per task, success rate, token use, failures."""
    from app.kanban import stats as kstats
    from app.kanban.schemas import COLUMNS
    async with KanbanSessionLocal() as s:
        cards, ops = await service.list_project_ops(s, project_key)
        cols = await service.list_columns(s, project_key)
    agent_columns = {c.name for c in cols if c.name not in COLUMNS}
    core = kstats.compute_core_stats(cards, ops, agent_columns)
    usage_by_agent = await kstats.gather_token_usage(core["session_to_agent"])
    tokens_available = kstats.apply_token_usage(core["agents"], usage_by_agent)
    return AgentStatsResponse(
        project_key=project_key,
        totals=core["totals"],
        agents=core["agents"],
        common_failures=core["common_failures"],
        tokens_available=tokens_available,
    )


@router.get("/wachtrij", response_model=WachtrijResponse)
async def po_wachtrij(project_key: str = Query(...)):
    """Return the PO-facing "wacht op jou" list — every card state that
    is blocked on a human decision, sorted oldest-first.

    Four detection categories, all reusing already-existing signals (no new
    column or workflow concept):

      * impediment_needs_answer — Impediment card with an open question
        (open KanbanGate or `**Impediment:**` comment without later
        `**Resolution:**`).
      * gate_open — any open KanbanGate regardless of column.
      * review_requested — review card whose
        ``metadata.reviewed_card_id`` is set.
      * awaiting_plan_ref — child card with ``parent_card_id`` but no
        ``kind='plan_ref'`` deliverable (the dispatcher holds these out
        until the analyst's ``add_plan_attachment`` lands).

    See kanban card `c7ea21b0…` and
    ``docs/cockpit/product-owner-volgbaarheid-analyse.md`` §2b/§4.1/§5 (kaart
    B). Unlike ``GET /cards``, this endpoint does NOT 404 on an unknown
    project key — a wachtrij is a *view*, and an empty board has no waiters.
    """
    async with KanbanSessionLocal() as s:
        rows = await service.po_wachtrij(s, project_key)
    return WachtrijResponse(
        project_key=project_key,
        total=len(rows),
        items=[WachtrijItem(**row) for row in rows],
    )


@router.get("/cards")
async def list_cards(
    project_key: str = Query(...),
    column: str | None = None,
    ready: bool | None = Query(
        None,
        description=(
            "When true, only return cards with no unmet dependencies "
            "(i.e. deps all Done, or none). When false, return the complement. "
            "Omit to skip the filter."
        ),
    ),
    blocking: bool | None = Query(
        None,
        description=(
            "When true, only return cards that at least one other non-Done "
            "card depends on. When false, return the complement. "
            "Omit to skip the filter."
        ),
    ),
    compact: bool = Query(
        False,
        description=(
            "When true, return only the dedupe-friendly per-card shape "
            "(id, title, column, work_type, rank) and skip description, "
            "deliverables, labels, metadata and the op-log-derived "
            "enrichments. Backwards-compatible opt-in: omit or set false "
            "to keep the full CardResponse shape every existing caller uses."
        ),
    ),
):
    async with KanbanSessionLocal() as s:
        # Refuse a typo'd/guessed project key instead of silently returning
        # [] (the false-empty-board failure mode MCP list_cards closed in
        # kanban card 91c85199). The same guard covers the REST MCP-`-32602`
        # fallback path documented in the dispatch prompt — a hand-typed
        # `project_key` from a dispatched agent used to look exactly like a
        # valid, empty project and downstream tools would happily write into
        # it. See _assert_project_key_known for the rationale.
        await _assert_project_key_known(s, project_key, for_create=False)
        rows = await service.list_cards(
            s, project_key, column,
            ready=ready, blocking=blocking, compact=compact,
        )
        if compact:
            return {"items": [
                CardSummaryResponse.model_validate(c).model_dump()
                for c in rows
            ]}
        items = []
        for c in rows:
            done_summary, completed_at = await service.enrich_done_info(s, c.id)
            impediment_status = await service.impediment_status_for_card(s, c)
            items.append(CardResponse.model_validate(c).model_copy(update={
                "done_summary": done_summary,
                "completed_at": completed_at,
                "impediment_status": impediment_status,
            }))
        return {"items": items}


async def _reload(s, cid: str) -> CardResponse:
    """Re-read a card after a mutation and shape it into a CardResponse.

    Stale-collection trap — read this before adding a handler that mutates
    `deliverables` or `attachments`. `service.get_card` eager-loads both via
    `selectinload`, and the session runs with `expire_on_commit=False`. A
    loader option does **not** re-populate a relationship that is already
    loaded on an identity-mapped instance (that needs `populate_existing()`),
    so if the same card object is still alive when `_reload` runs, this call
    hands back the pre-mutation collection.

    Two conditions must both hold for it to bite:

    1. A pre-commit `service.get_card` result is **bound to a live variable**
       (`card = await service.get_card(...)`). The identity map holds weak
       refs, so an unbound `if await service.get_card(...) is None:` is
       collected immediately and does *not* trigger this.
    2. The op changes collection **membership** (INSERT/DELETE of a
       deliverable/attachment row). An ORM-enabled UPDATE of an already
       loaded row synchronizes fine — that is why `update_plan_attachment`
       is safe despite binding `card`.

    So: for a pre-commit existence check in a handler that adds or removes a
    deliverable/attachment, use `await s.get(KanbanCard, cid)` — it leaves the
    relationships unloaded (see `upload_attachment`). `s.expire_all()` after
    the commit works too, at the cost of re-reading every attribute.
    """
    card = await service.get_card(s, cid)
    if card is None:
        raise HTTPException(404, "card not found")
    done_summary, completed_at = await service.enrich_done_info(s, cid)
    impediment_status = await service.impediment_status_for_card(s, card)
    return CardResponse.model_validate(card).model_copy(update={
        "done_summary": done_summary,
        "completed_at": completed_at,
        "impediment_status": impediment_status,
    })


@router.post("/cards", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
async def create_card(payload: CardCreate):
    async with KanbanSessionLocal() as s:
        # Refuse an unknown `project_key` unless the caller explicitly opts
        # into creating the very first card of a brand-new project
        # (`confirm_new_project=True`). The normal onboarding path is
        # `POST /kanban/enable`, which seeds the columns and makes the key
        # known — so an unknown key here is overwhelmingly a typo. This
        # mirrors MCP `create_card`'s gate from kanban card 91c85199; see
        # _assert_project_key_known for the rationale and
        # `CardCreate.confirm_new_project` for the opt-in semantics.
        if not payload.confirm_new_project:
            await _assert_project_key_known(
                s, payload.project_key, for_create=True
            )
        # Auto-fill `agent` from the work_type mapping when the caller did
        # not set it explicitly. See service.resolve_create_agent and
        # docs/cockpit/work-type-routing-analysis.md §2B. The resolved value
        # is written to the op-log so a rematerialize() rebuild reproduces it.
        # `confirm_new_project` is a guard signal, not card state — exclude
        # it from the persisted payload so it never round-trips into the
        # materialized row or survives a rematerialize() replay.
        payload_dict = payload.model_dump(
            exclude={"project_key", "confirm_new_project"}
        )
        payload_dict["agent"] = await service.resolve_create_agent(
            s, payload.project_key,
            work_type=payload.work_type,
            explicit_agent=payload.agent,
        )
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=payload.project_key, entity_id=None,
            payload=payload_dict)
        # If the caller set analyst_agent_id at create time, ensure the
        # analyst column exists for this project. Matches the PATCH path.
        if payload.analyst_agent_id:
            await service.ensure_analyst_column(s, payload.project_key)
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/reorder")
async def reorder_cards(payload: ReorderRequest):
    """Reassign ranks so cards in `column` follow `ordered_ids`. Emits rank-only
    move ops (no column change, no dispatch side effects). Unknown ids are skipped
    and ids whose rank is already correct are left untouched."""
    width = max(4, len(str(len(payload.ordered_ids))))
    async with KanbanSessionLocal() as s:
        for i, cid in enumerate(payload.ordered_ids):
            card = await service.get_card(s, cid)
            if card is None or card.column != payload.column:
                continue
            new_rank = str(i).zfill(width)
            if card.rank == new_rank:
                continue
            await apply_operation(s, op_type="move", entity_type="card",
                project_key="", entity_id=cid, payload={"rank": new_rank})
        await s.commit()
        rows = await service.list_cards(s, payload.project_key, payload.column)
        return {"items": [CardResponse.model_validate(c) for c in rows]}


@router.get("/cards/{cid}", response_model=CardResponse)
async def get_card(cid: str):
    async with KanbanSessionLocal() as s:
        return await _reload(s, cid)


@router.get("/cards/{cid}/activity", response_model=list[ActivityEntry])
async def activity(cid: str):
    async with KanbanSessionLocal() as s:
        return await service.card_activity(s, cid)


@router.get("/cards/{cid}/usage")
async def card_usage(cid: str):
    """Per-dispatch token telemetry for a single card (kanban card 8a2ad986).

    Returns the aggregated input/output/cache tokens + model breakdowns for
    the spawned session that worked this card, derived from Claude Code's
    per-session JSONL transcript (no extra token cost in the session itself
    — the data already exists, we just read it).

    Response shape — matches `CardUsage` from
    `app.services.dispatch_usage_service` so the frontend can render the
    same breakdown widget it already uses on `/usage`.

    Returns 404 only when the card itself doesn't exist; a card with no
    dispatch breadcrumbs (legacy cards dispatched before this feature
    landed) returns `null` instead, so the UI can distinguish "unknown
    card" from "card without telemetry yet" without a 404 round-trip per
    row.
    """
    from app.services.dispatch_usage_service import (
        get_card_usage as _get_card_usage,
    )

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")
        usage = await _get_card_usage(card)
        if usage is None:
            return {"usage": None}
        return {"usage": _CardUsageResponse(
            session_id=usage.session_id,
            recorded_model=usage.recorded_model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            total_tokens=usage.total_tokens,
            total_cost_usd=usage.total_cost_usd,
            first_activity=usage.first_activity,
            last_activity=usage.last_activity,
            model_breakdowns=[
                _ModelBreakdownResponse(
                    model=b.model,
                    input_tokens=b.input_tokens,
                    output_tokens=b.output_tokens,
                    cache_creation_tokens=b.cache_creation_tokens,
                    cache_read_tokens=b.cache_read_tokens,
                    total_tokens=b.total_tokens,
                )
                for b in usage.model_breakdowns
            ],
        ).model_dump(mode="json")}


@router.get("/cards/{cid}/run-ledger", response_model=RunLedger)
async def card_run_ledger(cid: str):
    """Per-card run ledger — the task → context → files → tests →
    outcome+model spine stitched from existing durable sources, no new
    data flow (docs/cockpit/run-ledger-decision.md, kanban card aa8158e3).

    Every step is best-effort: a missing source (no branch deliverable
    yet, a gc'd worktree, no iteration-loop run) yields an
    `available=False` step with a `note`, never a 500 — same contract as
    GET .../usage. 404 only when the card itself doesn't exist. Token
    totals are NOT re-derived here — see the response's `usage_url`,
    which points at the existing `/usage` endpoint.
    """
    from app.kanban import run_ledger_service

    async with KanbanSessionLocal() as s:
        ledger = await run_ledger_service.build_run_ledger(s, cid)
        if ledger is None:
            raise HTTPException(404, "card not found")
        return ledger


@router.patch("/cards/{cid}", response_model=CardResponse)
async def update_card(cid: str, payload: CardUpdate):
    async with KanbanSessionLocal() as s:
        data = payload.model_dump(exclude_unset=True)
        column_change = data.pop("column", None)

        if column_change:
            card = await service.get_card(s, cid)
            if card is None:
                raise HTTPException(404, "card not found")

            await apply_operation(s, op_type="move", entity_type="card",
                project_key="", entity_id=cid,
                payload={"column": column_change})

        if data:
            # A work_type change that leaves the pinned `agent` untouched keeps
            # dispatch routing to the stale agent (the "explicit agent wins"
            # rule is intentional — see service.resolve_create_agent). Surface
            # that otherwise-silent decision as a visible board comment. Read
            # the current agent *before* applying the update.
            mismatch_comment = None
            if "work_type" in data and "agent" not in data:
                existing = await service.get_card(s, cid)
                if existing is not None:
                    mismatch_comment = await service.work_type_agent_mismatch_comment(
                        s, existing.project_key or _project_key_from_card(s, existing),
                        new_work_type=data["work_type"],
                        current_agent=existing.agent,
                    )

            await apply_operation(s, op_type="update", entity_type="card",
                project_key="", entity_id=cid, payload=data)

            if mismatch_comment:
                await apply_operation(s, op_type="comment", entity_type="comment",
                    project_key="", entity_id=cid,
                    payload={"text": mismatch_comment})

        # If the card now has analyst_agent_id set, ensure the 'analyst'
        # kanban_columns row exists for this project. Idempotent. Without
        # this, the analyst session's move-to-analyst-column op lands the
        # card in a phantom column that doesn't render in the UI.
        updated = await service.get_card(s, cid)
        if updated is not None and updated.analyst_agent_id:
            project_key = updated.project_key or _project_key_from_card(s, updated)
            await service.ensure_analyst_column(s, project_key)

        await s.commit()
        return await _reload(s, cid)


def _project_key_from_card(s, card) -> str:
    """Resolve project_key for a card whose project_key column may be empty
    (legacy rows). Falls back to the card's project_key; otherwise to a
    slug key derived from the card's title. Used only by the PATCH path
    where we just modified the card."""
    if card.project_key:
        return card.project_key
    return f"slug:{card.id[:8]}"


@router.post("/cards/{cid}/move", response_model=CardResponse)
async def move_card(cid: str, payload: MoveRequest):
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")

        # Auto-assign agent from column default if card has no explicit agent
        if card.agent is None:
            default_agent = await service.get_column_default_agent(
                s, card.project_key, payload.column
            )
            if default_agent:
                await apply_operation(s, op_type="update", entity_type="card",
                    project_key=card.project_key, entity_id=cid,
                    payload={"agent": default_agent})

        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/claim", response_model=CardResponse)
async def claim_card(cid: str, payload: ClaimRequest):
    async with KanbanSessionLocal() as s:
        try:
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="", entity_id=cid, payload=payload.model_dump())
        except ClaimRejected as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e))
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/release", response_model=CardResponse)
async def release_card(cid: str):
    async with KanbanSessionLocal() as s:
        await release_card_claim(s, card_id=cid, project_key="")
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/comment", response_model=CardResponse)
async def comment(cid: str, payload: CommentRequest):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/set-gate", response_model=CardResponse)
async def set_gate(cid: str, payload: SetGateRequest):
    """REST mirror of the MCP ``set_card_gate`` tool.

    Sets or clears the card's ``metadata.gated_on`` machine-readable business
    gate. ``gated_on`` truthy (non-empty string) writes the value verbatim and
    the dispatcher (``_is_gated``) holds the card out of auto-dispatch until
    cleared. Empty string or None clears the gate. Either way a
    ``**Gate:** set/cleared`` activity-feed comment is posted so the gate's
    history is visible without inspecting metadata — mirrors the kanban-
    conventions `**Gate:**` prefix pattern.

    See ``docs/cockpit/kanban-conventions.md`` §4 for the rationale + the
    choice of ``metadata.gated_on`` over alternatives (depends_on / scheduled_at
    / dedicated column).
    """
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")

        existing_meta = dict(card.meta or {})
        new_value = (payload.gated_on or "").strip() or None
        if new_value is None:
            existing_meta.pop("gated_on", None)
            action = "cleared"
        else:
            existing_meta["gated_on"] = new_value
            action = "set"

        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid,
            payload={"metadata": existing_meta})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": f"**Gate:** {action} via set-gate"
                              + (f" — {new_value}" if new_value else "")})
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/request-review", response_model=CardResponse,
             status_code=status.HTTP_201_CREATED)
async def request_review(cid: str, payload: ReviewRequest):
    """Flag doubt on a completed card and route it to the analyst for triage.

    Posts a `**Review requested:** <note>` comment on the original Done card and
    creates a new Backlog `Review: <title>` card (work_type=analysis → analyst
    persona) linked back via metadata.reviewed_card_id. Returns the new review
    card. 404 if the card is missing, 409 if it isn't currently in Done.
    """
    async with KanbanSessionLocal() as s:
        try:
            card = await service.request_review(s, cid, payload.note)
        except service.CardNotInDone as e:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"card is in {e.column!r}, not Done; review can only be "
                "requested on a completed card",
            )
        if card is None:
            raise HTTPException(404, "card not found")
        await s.commit()
        return await _reload(s, card.id)


@router.post("/cards/{cid}/reopen", response_model=CardResponse)
async def reopen_card(cid: str, payload: ReopenRequest):
    """Weerleg & heropen: post a `**Revisit:** <note>` comment on a Done
    card and move the *same* card back to Backlog so the dispatcher picks
    it up on the next tick.

    Distinct from `request_review`: a review spawns a sibling analysis
    card (analyst triage); a reopen moves the existing card back to the
    dispatch queue. The Done summary stays in the activity feed (distinct
    prefix), and the dispatcher injects the rebuttal into the spawned
    session's prompt via the `## REVISIT` section (see
    `dispatch.build_card_prompt`).

    404 when the card id is unknown, 409 when the card exists but isn't
    in Done. Returns the reopened card so the UI can refresh without a
    second round-trip.
    """
    async with KanbanSessionLocal() as s:
        try:
            card = await service.reopen_card(s, cid, payload.note)
        except service.CardNotInDone as e:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"card is in {e.column!r}, not Done; reopen can only be "
                "requested on a completed card",
            )
        if card is None:
            raise HTTPException(404, "card not found")
        await s.commit()
        return await _reload(s, card.id)


@router.post("/cards/{cid}/deliverables", response_model=CardResponse)
async def attach(cid: str, payload: AttachRequest):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/attachments", response_model=CardResponse,
             status_code=status.HTTP_201_CREATED)
async def upload_attachment(cid: str, file: UploadFile = File(...)):
    """Attach a screenshot to a card. The image is stored on disk and its
    absolute path is injected into the dispatch prompt so the spawned session
    can Read it (see dispatch.build_card_prompt)."""
    content = await file.read(settings.kanban_attachment_max_bytes + 1)
    async with KanbanSessionLocal() as s:
        # Existence check via s.get (not service.get_card) so the card's
        # attachments relationship stays unloaded — otherwise the post-commit
        # _reload would return the stale (pre-upload) collection from the
        # identity map. See the _reload docstring for the full trap.
        if await s.get(KanbanCard, cid) is None:
            raise HTTPException(404, "card not found")
        try:
            meta = attachment_store.save_attachment(cid, content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        await apply_operation(s, op_type="attach", entity_type="attachment",
            project_key="", entity_id=cid, payload=meta)
        await s.commit()
        return await _reload(s, cid)


@router.get("/cards/{cid}/attachments/{attachment_id}")
async def get_attachment_file(cid: str, attachment_id: str):
    """Serve the raw image bytes for a card attachment (used by the board UI
    to render thumbnails)."""
    async with KanbanSessionLocal() as s:
        row = (await s.execute(
            select(KanbanAttachment)
            .where(KanbanAttachment.card_id == cid)
            .where(KanbanAttachment.id == attachment_id)
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "attachment not found")
        if not Path(row.storage_path).is_file():
            raise HTTPException(404, "attachment file missing")
        return FileResponse(
            row.storage_path,
            media_type=row.mime_type or "application/octet-stream",
            filename=row.filename or None,
        )


@router.delete("/cards/{cid}/attachments/{attachment_id}", response_model=CardResponse)
async def delete_attachment(cid: str, attachment_id: str):
    async with KanbanSessionLocal() as s:
        row = (await s.execute(
            select(KanbanAttachment)
            .where(KanbanAttachment.card_id == cid)
            .where(KanbanAttachment.id == attachment_id)
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "attachment not found")
        storage_path = row.storage_path
        await apply_operation(s, op_type="detach", entity_type="attachment",
            project_key="", entity_id=cid, payload={"id": attachment_id})
        await s.commit()
        attachment_store.unlink_attachment(storage_path)
        return await _reload(s, cid)


@router.patch("/cards/{cid}/plan-attachment", response_model=CardResponse)
async def update_plan_attachment(cid: str, payload: UpdatePlanAttachmentRequest):
    """Overwrite the markdown on the card's existing `kind=plan` deliverable.

    Returns 404 when no `kind=plan` deliverable exists for the card — use
    POST /cards/{cid}/deliverables (or the MCP `add_plan_attachment` tool) to
    create one first. The deliverable row id is preserved across updates so
    child `plan_ref` rows keep resolving to the same plan on the next dispatch.
    """
    from app.kanban.models import KanbanDeliverable
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")
        existing = (await s.execute(
            select(KanbanDeliverable)
            .where(KanbanDeliverable.card_id == cid)
            .where(KanbanDeliverable.kind == "plan")
            .order_by(KanbanDeliverable.created_at.desc())
            .limit(1)
        )).scalars().first()
        if existing is None:
            raise HTTPException(
                404,
                "no plan attachment on this card; create one first",
            )
        await apply_operation(s, op_type="update_plan_attachment",
            entity_type="deliverable", project_key="",
            entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


# Cap mirrors backend/app/kanban/mcp_server.MAX_CHILDREN_PER_PLAN. Keep the
# two constants in sync — bumping one without the other would let the REST
# path accept a plan the MCP path would reject (or vice versa).
_MAX_CHILDREN_PER_PLAN = 50


@router.post("/cards/{cid}/plan-attachment",
             response_model=AddPlanAttachmentResponse)
async def add_plan_attachment(cid: str, payload: AddPlanAttachmentRequest):
    """REST mirror of the MCP `add_plan_attachment` tool.

    Persists a plan on a parent card and wires `plan_ref` deliverables to each
    child. Identical semantics to the MCP version — same op-log, same
    validation (parent existence, parent_mismatch, child_not_found,
    cycle_detected, too_many_children). Use this entry point when the
    kanban MCP layer is unreachable (e.g. its working directory was removed
    out from under it by `worktree-gc.sh`).

    Returns the new `plan_deliverable_id` plus the wired child card ids on
    success. On validation failure returns 4xx with the error code in the
    `detail` field (matches the MCP error-dict contract).
    """
    from app.kanban import dep_resolver
    from app.kanban.models import KanbanCard, KanbanDeliverable

    if len(payload.child_card_ids) > _MAX_CHILDREN_PER_PLAN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "too_many_children",
                "max": _MAX_CHILDREN_PER_PLAN,
            },
        )

    deps = payload.depends_on_graph or {}
    cycle = dep_resolver.detect_cycle(
        {c: list(deps.get(c, []) or []) for c in payload.child_card_ids}
    )
    if cycle is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "cycle_detected", "cycle": cycle},
        )

    async with KanbanSessionLocal() as s:
        parent = await s.get(KanbanCard, cid)
        if parent is None:
            raise HTTPException(404, "card not found")

        for child_id in payload.child_card_ids:
            child = await s.get(KanbanCard, child_id)
            if child is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"error": "child_not_found", "card_id": child_id},
                )
            if child.parent_card_id != cid:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "parent_mismatch",
                        "card_id": child_id,
                        "expected_parent": cid,
                    },
                )

        project_key = parent.project_key
        await apply_operation(
            s, op_type="add_plan_attachment", entity_type="deliverable",
            project_key=project_key, entity_id=cid,
            payload={"plan_markdown": payload.plan_markdown},
        )
        plan_deliverable_id = (
            await s.execute(
                select(KanbanDeliverable)
                .where(KanbanDeliverable.card_id == cid,
                       KanbanDeliverable.kind == "plan")
                .order_by(KanbanDeliverable.created_at.desc())
            )
        ).scalars().first().id

        for child_id in payload.child_card_ids:
            await apply_operation(
                s, op_type="link_plan_ref", entity_type="deliverable",
                project_key=project_key, entity_id=child_id,
                payload={"ref_json": json.dumps({
                    "parent_card_id": cid,
                    "plan_deliverable_id": plan_deliverable_id,
                }), "depends_on": list(deps.get(child_id, []) or [])},
            )
        await s.commit()

    return AddPlanAttachmentResponse(
        parent_card_id=cid,
        plan_deliverable_id=plan_deliverable_id,
        child_card_ids=list(payload.child_card_ids),
    )


@router.post("/cards/{cid}/gates", response_model=GateResponse, status_code=status.HTTP_201_CREATED)
async def open_gate(cid: str, payload: GateOpenRequest):
    """Open a decision gate: a question + structured choices rendered in the
    UI. Also logs a comment so it shows up in the card's activity feed."""
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")
        gate = await service.create_gate(s, card_id=cid, project_key=card.project_key,
            question=payload.question, options=payload.options)
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": f"**Gate:** {payload.question}"})
        await s.commit()
        return GateResponse.model_validate(gate)


@router.get("/cards/{cid}/gates", response_model=list[GateResponse])
async def list_gates(cid: str):
    async with KanbanSessionLocal() as s:
        gates = await service.list_gates(s, cid)
        return [GateResponse.model_validate(g) for g in gates]


@router.post("/gates/{gate_id}/answer", response_model=GateResponse)
async def answer_gate(gate_id: str, payload: GateAnswerRequest):
    async with KanbanSessionLocal() as s:
        try:
            gate = await service.answer_gate(s, gate_id, payload.answer)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if gate is None:
            raise HTTPException(404, "gate not found")
        await s.commit()
        return GateResponse.model_validate(gate)


@router.post("/enable")
async def enable(payload: EnableRequest, request: Request):
    path = Path(payload.project_path)
    if not path.is_dir():
        raise HTTPException(422, "project_path is not a directory")
    key = f"slug:{payload.slug}" if payload.slug else resolve_project_key(str(path))
    mcp_file = path / ".mcp.json"
    data = {}
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text())
        except json.JSONDecodeError:
            data = {}
    entry: dict = {"type": "sse", "url": f"{resolve_base_url(request)}/kanban-mcp/sse"}
    if settings.api_token:
        entry["headers"] = {"Authorization": f"Bearer {settings.api_token}"}
    data.setdefault("mcpServers", {})["cockpit-kanban"] = entry
    _write_json_atomic(mcp_file, data)

    # Ensure fixed columns exist for this project (Backlog, Impediment, Done)
    # Agent columns are created dynamically based on project agents
    from app.kanban.schemas import COLUMNS
    async with KanbanSessionLocal() as s:
        existing = await service.list_columns(s, key)
        existing_names = {c.name for c in existing}
        
        # Get agents from .claude/agents directory
        agents_dir = path / ".claude" / "agents"
        agents = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
        valid_names = set(COLUMNS) | set(agents)
        
        # Move cards from orphaned columns to Backlog before removing columns
        orphaned = [c.name for c in existing if c.name not in valid_names]
        if orphaned:
            from sqlalchemy import update

            from app.kanban.models import KanbanCard
            for col_name in orphaned:
                await s.execute(
                    update(KanbanCard)
                    .where(KanbanCard.project_key == key)
                    .where(KanbanCard.column == col_name)
                    .values(column="Backlog")
                )
        
        # Remove orphaned columns
        for col in existing:
            if col.name not in valid_names:
                await service.delete_column(s, col.id)
        
        # Create missing fixed columns
        for i, col_name in enumerate(COLUMNS):
            if col_name not in existing_names:
                await service.create_column(s, key, name=col_name, rank=f"{i:04d}")
        
        # Sync agent columns
        await service.sync_agent_columns(s, key, agents)
        
        await s.commit()

    return {"project_key": key, "enabled": True}


@router.post("/disable")
async def disable(payload: EnableRequest):
    path = Path(payload.project_path)
    mcp_file = path / ".mcp.json"
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text())
            data.get("mcpServers", {}).pop("cockpit-kanban", None)
            _write_json_atomic(mcp_file, data)
        except json.JSONDecodeError:
            pass
    return {"enabled": False}


@router.get("/mcp-status")
async def mcp_status(project_path: str = Query(...)):
    path = Path(project_path)
    mcp_file = path / ".mcp.json"
    if not mcp_file.exists():
        return {"enabled": False}
    try:
        data = json.loads(mcp_file.read_text())
        enabled = "cockpit-kanban" in data.get("mcpServers", {})
    except (json.JSONDecodeError, OSError):
        enabled = False
    return {"enabled": enabled}


@router.get("/mcp-health")
async def mcp_health():
    """End-to-end self-check of the kanban MCP wiring (advertised endpoint routes
    to the mount, tools registered, store reachable). Surfaced in the UI so a
    silent mis-mount is visible instead of only manifesting as agents that never
    touch their cards."""
    from app.kanban.mcp_health import check_mcp_health
    return await check_mcp_health()


@router.get("/project-key")
async def project_key(project_path: str = Query(...)):
    return {"project_key": resolve_project_key(project_path)}


@router.get("/autodispatch")
async def get_autodispatch(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "enabled": await dispatch.is_autodispatch_enabled(s, project_key)}


@router.post("/autodispatch")
async def set_autodispatch(payload: AutodispatchRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, payload.project_key, payload.enabled)
        await s.commit()
    return {"project_key": payload.project_key, "enabled": payload.enabled}


@router.post(
    "/projects/from-intake",
    response_model=CreateProjectFromIntakeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_from_intake(payload: CreateProjectFromIntakeRequest):
    """REST mirror of the MCP `create_project_from_intake` tool.

    Drives the inceptie-pipeline (kanban card c33b2f14, facet A of
    platform-as-app-factory — `docs/cockpit/product-inceptie-pipeline.md`
    §4 optie 2). The action is atomic: any failure between the 6 steps
    rolls back filesystem + kanban-DB + Project row + autodispatch-meta
    so the system is never left half-registered. The intake card lands on
    Done with a `**Promoted to project:** …` comment when the action
    succeeds, providing a one-glance audit trail.

    Returns the new `project_id`, the resolved `new_project_key`
    (slug:<basename> for git-init repos without a remote), and the id of
    the first kanban card placed in the new project's Backlog.
    """
    from app.database import AsyncSessionLocal
    from app.services.inception_service import InceptionService

    try:
        async with KanbanSessionLocal() as ks, AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_intake(
                intake_card_id=payload.intake_card_id,
                project_name=payload.project_name,
                target_path=payload.target_path,
            )
        # 201 on creation; the result shape is the response model.
        return CreateProjectFromIntakeResponse(**result)
    except ValueError as e:
        # Validation failures (card not found, wrong column, project already
        # registered at target path) — the action did NOT touch anything.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except FileExistsError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except RuntimeError as e:
        # git init failed (or any sub-step before commit). The action's own
        # rollback ran, but surface the failure so the caller knows nothing
        # landed.
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


@router.get("/dispatch-pause")
async def get_dispatch_pause():
    """Whether auto-dispatch is globally paused after a Claude usage-limit hit,
    and until when. Not scoped to a project: the underlying limit is account-wide.

    ``paused_providers`` lists every provider whose per-provider pause slot is
    still in the future (independent of the legacy global ``paused`` flag -- a
    per-provider pause does NOT trip the global flag and vice versa). Frontend
    banner code consumes this list to render "minimax is paused" without a
    second endpoint. Empty list when nothing is paused.
    """
    from app.kanban import dispatch_pause
    async with KanbanSessionLocal() as s:
        paused = await dispatch_pause.is_dispatch_paused(s)
        paused_until = await dispatch_pause.get_paused_until(s) if paused else None
        paused_providers = await dispatch_pause.list_paused_providers(s)
        manually_paused_providers = (
            await dispatch_pause.list_manually_paused_providers(s)
        )
    return {"paused": paused,
            "paused_until": paused_until.isoformat() if paused_until else None,
            "paused_providers": paused_providers,
            "manually_paused_providers": manually_paused_providers}


@router.delete("/dispatch-pause")
async def clear_dispatch_pause():
    """Manually clear the global auto-dispatch pause -- an operator override for
    when the automatic 429-detection was wrong. Idempotent: clearing when
    nothing is paused is a no-op that reports {cleared: false, was_paused: false}.

    Also wipes every per-provider pause slot via ``clear_all_pauses`` -- a
    single operator click un-freezes the whole device, regardless of which
    provider reported the limit. ``was_paused`` still reflects the legacy global
    pause state (the audit-comment-on-To-Resume contract that downstream
    consumers rely on is keyed off it); the per-provider clear runs even when
    no global pause was active, so an operator who only sees a per-provider
    banner can still un-freeze via this endpoint.
    """
    from app.kanban import dispatch, dispatch_pause
    async with KanbanSessionLocal() as s:
        # Snapshot every pause kind BEFORE clearing so the response correctly
        # reports "we cleared something". FCR-blokkade: the previous flow
        # only inspected the legacy global slot — a manual-only pause (no
        # global pause set) was silently wiped but the response said
        # `cleared=false`, which the banner treated as a failure and
        # displayed a false error toast.
        global_paused = await dispatch_pause.is_dispatch_paused(s)
        time_paused = await dispatch_pause.list_paused_providers(s)
        manual_paused = await dispatch_pause.list_manually_paused_providers(s)
        any_paused = bool(global_paused or time_paused or manual_paused)
        # `was_paused` keeps its historical contract: it reflects the legacy
        # global pause state, which the audit-comment-on-To-Resume contract
        # downstream consumers rely on is keyed off.
        cleared, was_paused = await dispatch.clear_dispatch_pause(s)
        await dispatch_pause.clear_all_pauses(s)
        # Commit whenever the legacy clear ran (to persist the audit comment
        # + global clear) OR whenever any per-provider pause existed (to
        # persist the wipe). Without this a per-provider-only pause set
        # without the global pause would be rolled back when the session
        # exits the `async with` block.
        await s.commit()
    logger.info(
        "dispatch-pause manually cleared via API "
        "(was_paused=%s, time_paused=%s, manual_paused=%s)",
        was_paused, time_paused, manual_paused,
    )
    # `cleared` reflects "anything was paused and we wiped it" — covers the
    # manual-only case the old `cleared=cleared` (which only mirrored
    # `was_paused`) mis-reported.
    return {"cleared": any_paused, "was_paused": was_paused}


# Mirrors the allow-list in app.kanban.subscription_pool._ALLOWED_POOL_PROVIDERS
# so the manual-pause toggle uses the same vocabulary as the existing pool /
# override surfaces. Adding a new provider is one edit in provider_env.py
# plus this tuple -- three surfaces share the same source of truth.
_MANUAL_PAUSE_PROVIDERS = (
    "anthropic", "bedrock", "minimax", "anthropic-compatible",
)


@router.put("/dispatch-pause/subscription/{provider}")
async def set_subscription_pause(provider: str, payload: dict):
    """Toggle the operator manual pause for a single subscription
    (kaart f056b2888a...).

    Body: ``{"paused": bool}``. ``paused=true`` writes the
    ``dispatch_paused_manual:<provider>`` slot; ``paused=false`` clears it.
    Idempotent -- toggling the same direction twice is a no-op, and toggling
    off an already-unpaused provider is a no-op.

    Independent from the time-based ``dispatch_paused_until:<provider>`` slot:
    an auto-tripped limit and an operator toggle can coexist on the same
    provider, and either being active keeps dispatch off. The bulk-clear
    DELETE /dispatch-pause wipes both kinds in one click.

    Unknown providers are rejected with 422 so the operator sees the
    rejection at toggle time -- and the dispatch gate never queries an
    unknown subscription.
    """
    if provider not in _MANUAL_PAUSE_PROVIDERS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown provider: {provider!r}; "
            f"expected one of {list(_MANUAL_PAUSE_PROVIDERS)}",
        )
    if not isinstance(payload, dict) or "paused" not in payload:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "body must be a JSON object with a 'paused' boolean",
        )
    paused = payload["paused"]
    if not isinstance(paused, bool):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "'paused' must be a JSON boolean",
        )
    from app.kanban import dispatch_pause
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_manual_pause(s, provider, paused)
        await s.commit()
        manually_paused_providers = (
            await dispatch_pause.list_manually_paused_providers(s)
        )
    logger.info(
        "dispatch-pause subscription manually set via API "
        "(provider=%s, paused=%s)", provider, paused,
    )
    return {
        "provider": provider,
        "paused": paused,
        "manually_paused_providers": manually_paused_providers,
    }


@router.get("/shipmode")
async def get_shipmode(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "mode": await dispatch.get_ship_mode(s, project_key)}


@router.post("/shipmode")
async def set_shipmode(payload: ShipModeRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_ship_mode(s, payload.project_key, payload.mode)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "mode": payload.mode}


@router.get("/skip-permissions")
async def get_skip_permissions(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "enabled": await dispatch.get_skip_permissions(s, project_key)}


@router.post("/skip-permissions")
async def set_skip_permissions(payload: SkipPermissionsRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        await dispatch.set_skip_permissions(s, payload.project_key, payload.enabled)
        await s.commit()
    return {"project_key": payload.project_key, "enabled": payload.enabled}


@router.get("/transport")
async def get_transport(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "transport": await dispatch.get_default_transport(s, project_key)}


@router.post("/transport")
async def set_transport(payload: DefaultTransportRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_default_transport(s, payload.project_key, payload.transport)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "transport": payload.transport}


@router.get("/subscription-override")
async def get_subscription_override(project_key: str = Query(...)):
    """Read the board-wide active-subscription-override (fase 0 / quick win).

    Returns ``{"project_key": ..., "override": <dict|None>}``. ``None`` means
    no pin is set — the dispatcher falls back to per-column defaults exactly
    as it does today. Mirrors the get/set shape of the shipmode / transport
    endpoints so the frontend can reuse the same fetching pattern.
    """
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {
            "project_key": project_key,
            "override": await dispatch.get_active_subscription_override(
                s, project_key,
            ),
        }


@router.post("/subscription-override")
async def set_subscription_override(payload: ActiveSubscriptionOverrideRequest):
    """Set or clear the board-wide active-subscription-override.

    Pass ``override: null`` to clear (dispatcher falls back to per-column
    defaults). Otherwise ``override`` is ``{provider: str, model?: str|null}``
    — an unknown provider is rejected with 422 so the caller knows nothing
    landed.
    """
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_active_subscription_override(
                s, payload.project_key, payload.override,
            )
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {
        "project_key": payload.project_key,
        "override": payload.override,
    }


@router.get("/subscription-pool")
async def get_subscription_pool(project_key: str = Query(...)):
    """Read the subscription pool (fase 1b of the analyse).

    Returns ``{"project_key": ..., "pool": <list[PoolEntry]|None>}``.
    ``None`` means no pool is configured — the dispatcher falls back to
    today's column-default chain exactly as before. Each ``PoolEntry``
    is shaped as ``{cli, provider, model|null, drempel}`` so the frontend
    can render it verbatim without per-field reshaping. Kaart 8f40d443…:
    the per-entry ``cli`` field is again first-class and consumed by
    the router (it was briefly dropped in kaart 0b3ad6e2… and is now
    required again to honour the per-CLI quota axis). The default
    ``cli`` value (``subscription_pool.DEFAULT_POOL_CLI``,
    ``"claude-code"``) is back-filled on read for rows that omit it,
    so legacy stored payloads still load."""
    from app.kanban import subscription_pool as pool_mod
    async with KanbanSessionLocal() as s:
        entries = await pool_mod.get_subscription_pool(s, project_key)
    if entries is None:
        return {"project_key": project_key, "pool": None}
    return {
        "project_key": project_key,
        "pool": [
            {"cli": e.cli, "provider": e.provider,
             "model": e.model, "drempel": e.drempel}
            for e in entries
        ],
    }


@router.post("/subscription-pool")
async def set_subscription_pool(payload: SubscriptionPoolRequest):
    """Set or clear the subscription pool.

    Pass ``pool: null`` to clear (dispatcher falls back to per-column
    defaults exactly as today). Otherwise ``pool`` is a non-empty list
    of ``{cli, provider, model?, drempel}`` entries — providers are
    validated against the same allow-list as the active-subscription-
    override, drempel must be in (0, 1], and ``cli`` must be non-empty.
    Invalid input is rejected with 422 so the caller knows nothing
    landed."""
    from app.kanban import subscription_pool as pool_mod
    async with KanbanSessionLocal() as s:
        try:
            await pool_mod.set_subscription_pool(
                s, payload.project_key, payload.entries,
            )
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {
        "project_key": payload.project_key,
        "pool": payload.pool,
    }


@router.delete("/cards/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(cid: str, force: bool = Query(False)):
    from app.kanban.session_cleanup import find_worktree_unmerged_warning

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is not None:
            warning = await find_worktree_unmerged_warning(card)
            if warning is not None:
                if not force:
                    parts = []
                    if warning["ahead"] > 0:
                        parts.append(
                            f"{warning['ahead']} commit(s) on branch '{warning['branch']}' "
                            f"not yet merged into {warning['default_branch']}"
                        )
                    if warning["dirty"]:
                        parts.append("uncommitted changes")
                    logger.warning(
                        "blocked delete of card %s %r: unmerged worktree %s (%s)",
                        cid, card.title, warning["worktree_path"], " and ".join(parts),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"This card's worktree still has {' and '.join(parts)} "
                            f"({warning['worktree_path']}). Deleting the card won't delete "
                            f"the work, but it will no longer be tracked on the board. "
                            f"Retry with force=true to delete anyway."
                        ),
                    )
                logger.warning(
                    "force-deleting card %s %r despite unmerged worktree %s",
                    cid, card.title, warning["worktree_path"],
                )
        # Dep-aware guard: strip this card out of any non-Done dependent's
        # depends_on (+ audit comment) before the hard delete, so a satisfied
        # dependency never silently becomes a permanent fail-closed block. See
        # docs/cockpit/dangling-depends-on-analyse.md §1.2/§4.
        await service.strip_dangling_deps_on_delete(s, cid)
        await apply_operation(s, op_type="delete", entity_type="card",
            project_key="", entity_id=cid, payload={})
        await s.commit()


@router.get("/agents")
async def list_agents(project_path: str = Query(...)):
    """Agent personas available to this project (\"<name>\" of each
    .claude/agents/<name>.md), for the per-card agent selector."""
    agents_dir = Path(project_path) / ".claude" / "agents"
    names = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    return {"agents": names}


@router.post("/sync-agent-columns")
async def sync_agent_columns_endpoint(payload: EnableRequest):
    """Sync agent columns with the agents configured for this project."""
    path = Path(payload.project_path)
    if not path.is_dir():
        raise HTTPException(422, "project_path is not a directory")
    
    # Get project key
    key = f"slug:{payload.slug}" if payload.slug else resolve_project_key(str(path))
    
    # Get agents from .claude/agents directory
    agents_dir = path / ".claude" / "agents"
    agents = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    
    async with KanbanSessionLocal() as s:
        await service.sync_agent_columns(s, key, agents)
        await s.commit()
    
    return {"project_key": key, "agents": agents, "synced": True}


@router.post("/cards/{cid}/dispatch")
async def dispatch_now(cid: str, payload: DispatchRequest):
    """Manually run the chosen agent on one card now, regardless of auto-pick."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            res = await dispatch.dispatch_card(s, card_id=cid,
                project_path=payload.project_path, agent_override=payload.agent)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"dispatch failed: {e}")
        await s.commit()
    if res is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
            "could not dispatch (card missing or already claimed)")
    return res


@router.post("/cards/{cid}/redispatch")
async def redispatch_now(cid: str, payload: RedispatchRequest):
    """Release a stuck card and re-dispatch it with a fresh session."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            res = await dispatch.redispatch_card(
                s, card_id=cid, project_path=payload.project_path,
                agent_override=payload.agent,
                # The REST handler is the CardDrawer's Redispatch button —
                # label it `ui` so an operator investigating a redispatch
                # from the activity feed sees a human-facing source.
                # Programmatically called REST clients (e.g. an external
                # orchestrator) MAY override via `caller_source` in the
                # request body for their own identity; the default `ui` is
                # still appropriate when they don't (or as a fallback).
                caller_source=payload.caller_source or "ui",
            )
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"redispatch failed: {e}")
        await s.commit()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card not found")
    return res


@router.post("/cards/{cid}/take-over")
async def take_over(cid: str, payload: TakeOverRequest):
    """Promote a headless-dispatched card's session to an attachable tmux pane.

    Implements `docs/cockpit/human-takeover-headless-decision.md` §7: ends the
    headless subprocess (if still alive), spawns `claude --resume <session_id>`
    in tmux under the same session_name, and leaves the `agent:` claim,
    branch, and worktree untouched. The existing `CardRunTab` "Live" view
    picks up the new tmux session automatically — no separate attach step.
    """
    from app.kanban import takeover
    key = resolve_project_key(payload.project_path)
    async with KanbanSessionLocal() as s:
        try:
            result = await takeover.promote_to_tmux(
                s, card_id=cid, project_key=key, project_path=payload.project_path,
            )
        except takeover.TakeoverError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e))
        await s.commit()
    return result


@router.post("/redispatch-all")
async def redispatch_all(payload: EnableRequest):
    """Re-dispatch all orphaned cards (unclaimed on agent columns) for a project."""
    from app.kanban import dispatch
    from app.kanban.project_key import resolve_project_key
    key = resolve_project_key(payload.project_path)
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=key, project_path=payload.project_path,
        )
        await s.commit()
    return {"redispatched": len(results), "results": results}


@router.post("/dispatch-all")
async def dispatch_all(payload: EnableRequest):
    """Dispatch all pending Backlog/Dispatch cards for a project at once."""
    from app.kanban import dispatch
    from app.kanban.project_key import resolve_project_key
    key = resolve_project_key(payload.project_path)
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=key, project_path=payload.project_path,
        )
        await s.commit()
    return {"dispatched": len(results), "results": results}


@router.post("/clear-column")
async def clear_column(payload: ColumnClearRequest):
    """Delete all cards in a given column for a project."""
    async with KanbanSessionLocal() as s:
        cards = await service.list_cards(s, payload.project_key, column=payload.column)
        count = 0
        for card in cards:
            # Same dep-aware guard as the single-card delete: strip each cleared
            # card out of any non-Done dependent's depends_on (+ audit comment)
            # so "Clear Done" never orphans a satisfied dependency into a
            # permanent fail-closed block. dangling-depends-on-analyse.md §1.2/§4.
            await service.strip_dangling_deps_on_delete(s, card.id)
            await apply_operation(s, op_type="delete", entity_type="card",
                project_key="", entity_id=card.id, payload={})
            count += 1
        await s.commit()
    return {"cleared": count}


@router.post("/cards/{cid}/resolve-impediment", response_model=CardResponse)
async def resolve_impediment(cid: str, payload: ImpedimentResolveRequest):
    """Resolve an impediment by dispatching to a specific agent.

    Composes the resumed session's `## IMPEDIMENT` prompt section from up to
    three sources, in the order they appear in `build_card_prompt`:

    1. The most recent `**Impediment:**` comment (set by `report_impediment`).
    2. If the card has an answered KanbanGate (the human picked one of the
       structured options `report_impediment(options=[...])` advertised),
       that pick is forwarded as `impediment_answer` so the resumed session
       sees the human's decision as authoritative — even when the resolver
       didn't supply an `answer` field of their own.
    3. If `payload.answer` is supplied, it's stamped as a durable
       `**Resolution:**` comment (free-text path) and read back via
       `dispatch.extract_impediment_answer` so it survives a re-resolve.

    When both (2) and (3) are present, the gate pick wins — it's the more
    recent, structured decision from the dedicated choice UI.
    """
    from app.kanban import dispatch

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        if card is None:
            raise HTTPException(404, "card not found")

        if card.column != "Impediment":
            raise HTTPException(422, "card is not in Impediment column")

        # A human's answer, when supplied, is stamped as a durable
        # `**Resolution:**` comment before we re-read the feed — that makes it
        # auditable and lets extract_impediment_answer pick it up uniformly
        # (whether it came in on this request or via a manual comment).
        if payload.answer and payload.answer.strip():
            await apply_operation(s, op_type="comment", entity_type="comment",
                project_key="", entity_id=cid,
                payload={"text": f"{dispatch._IMPEDIMENT_ANSWER_PREFIX}{payload.answer.strip()}"})
            await s.commit()

        # Get impediment question + latest human answer from activity. Match the
        # exact `**Impediment:** ` prefix (not a loose "Impediment:" substring)
        # so a human's `**Resolution:**` answer that happens to mention the word
        # isn't mistaken for the question.
        activity = await service.card_activity(s, cid)
        impediment_question = None
        for entry in reversed(activity):
            text = entry.payload.get("text", "") if entry.op_type == "comment" else ""
            if text.startswith("**Impediment:** "):
                impediment_question = text[len("**Impediment:** "):]
                break

        if not impediment_question:
            impediment_question = "No impediment question found"

        # Resolve in priority order:
        # 1. Structured-options gate answer (the new report_impediment
        #    options= path): when the human clicked a choice button, that's the
        #    most recent, structured decision — it wins over a free-text
        #    resolution comment on the same card.
        # 2. Free-text `**Resolution:**` comment (legacy / resolve-impediment
        #    payload.answer path).
        gate_answer = await service.latest_gate_answer(s, cid)
        if gate_answer is not None:
            impediment_answer = gate_answer
        else:
            impediment_answer = dispatch.extract_impediment_answer(activity)

        # Determine target agent based on workflow rules or override
        target_agent = payload.target_agent
        if not target_agent:
            current_agent = card.agent or "engineer"
            possible_agents = _IMPEDIMENT_AGENTS.get(current_agent, ["engineer"])
            target_agent = possible_agents[0] if possible_agents else "engineer"

        try:
            res = await dispatch.dispatch_impediment_card(
                s, card_id=cid, project_path=payload.project_path,
                target_agent=target_agent, impediment_question=impediment_question,
                impediment_answer=impediment_answer,
            )
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"dispatch failed: {e}")
        await s.commit()

    if res is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
            "could not dispatch impediment (card missing or already claimed)")
    return await _reload(s, cid)
