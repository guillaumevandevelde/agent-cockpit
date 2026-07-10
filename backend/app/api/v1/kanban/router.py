"""REST API for the kanban board. All mutations go through apply_operation."""
import json
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.config import settings
from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import ClaimRejected, apply_operation
from app.kanban.project_key import resolve_project_key
from app.kanban.schemas import (
    WORK_TYPES,
    ActivityEntry,
    AddPlanAttachmentRequest,
    AddPlanAttachmentResponse,
    AgentStatsResponse,
    AttachRequest,
    AutodispatchRequest,
    CardCreate,
    CardResponse,
    CardUpdate,
    ClaimRequest,
    ColumnClearRequest,
    ColumnCreate,
    ColumnResponse,
    ColumnUpdate,
    CommentRequest,
    DefaultTransportRequest,
    DispatchRequest,
    EnableRequest,
    GateAnswerRequest,
    GateOpenRequest,
    GateResponse,
    ImpedimentResolveRequest,
    MaxSessionsRequest,
    MoveRequest,
    RedispatchRequest,
    ReorderRequest,
    ReopenRequest,
    ReviewRequest,
    ShipModeRequest,
    SkipPermissionsRequest,
    UpdatePlanAttachmentRequest,
    WorkTypeMappingBulk,
    WorkTypeMappingResponse,
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


router = APIRouter(prefix="/kanban", tags=["Kanban"])


@router.get("/columns")
async def columns(project_key: str = Query(...)):
    async with KanbanSessionLocal() as s:
        cols = await service.list_columns(s, project_key)
        return {"columns": [ColumnResponse.model_validate(c) for c in cols]}


@router.post("/columns", response_model=ColumnResponse, status_code=status.HTTP_201_CREATED)
async def create_column(payload: ColumnCreate):
    async with KanbanSessionLocal() as s:
        col = await service.create_column(
            s, project_key=payload.project_key, name=payload.name,
            rank=payload.rank, default_agent=payload.default_agent,
            default_provider=payload.default_provider,
            default_model=payload.default_model,
            max_sessions=payload.max_sessions,
        )
        await s.commit()
        return ColumnResponse.model_validate(col)


@router.patch("/columns/{column_id}", response_model=ColumnResponse)
async def update_column(column_id: str, payload: ColumnUpdate):
    async with KanbanSessionLocal() as s:
        col = await service.update_column(
            s, column_id,
            name=payload.name, rank=payload.rank,
            default_agent=payload.default_agent,
            default_provider=payload.default_provider,
            default_model=payload.default_model,
            max_sessions=payload.max_sessions,
        )
        if col is None:
            raise HTTPException(404, "column not found")
        await s.commit()
        return ColumnResponse.model_validate(col)


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
):
    async with KanbanSessionLocal() as s:
        rows = await service.list_cards(
            s, project_key, column,
            ready=ready, blocking=blocking,
        )
        items = []
        for c in rows:
            done_summary, completed_at = await service.enrich_done_info(s, c.id)
            items.append(CardResponse.model_validate(c).model_copy(update={
                "done_summary": done_summary,
                "completed_at": completed_at,
            }))
        return {"items": items}


async def _reload(s, cid: str) -> CardResponse:
    card = await service.get_card(s, cid)
    if card is None:
        raise HTTPException(404, "card not found")
    done_summary, completed_at = await service.enrich_done_info(s, cid)
    return CardResponse.model_validate(card).model_copy(update={
        "done_summary": done_summary,
        "completed_at": completed_at,
    })


@router.post("/cards", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
async def create_card(payload: CardCreate):
    async with KanbanSessionLocal() as s:
        # Auto-fill `agent` from the work_type mapping when the caller did
        # not set it explicitly. See service.resolve_create_agent and
        # docs/cockpit/work-type-routing-analysis.md §2B. The resolved value
        # is written to the op-log so a rematerialize() rebuild reproduces it.
        payload_dict = payload.model_dump(exclude={"project_key"})
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
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=cid, payload={})
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/comment", response_model=CardResponse)
async def comment(cid: str, payload: CommentRequest):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid, payload=payload.model_dump())
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


@router.get("/dispatch-pause")
async def get_dispatch_pause():
    """Whether auto-dispatch is globally paused after a Claude usage-limit hit,
    and until when. Not scoped to a project: the underlying limit is account-wide."""
    from app.kanban import dispatch_pause
    async with KanbanSessionLocal() as s:
        paused = await dispatch_pause.is_dispatch_paused(s)
        paused_until = await dispatch_pause.get_paused_until(s) if paused else None
    return {"paused": paused,
            "paused_until": paused_until.isoformat() if paused_until else None}


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


@router.get("/max-sessions")
async def get_max_sessions(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "max_sessions": await dispatch.get_max_sessions(s, project_key)}


@router.post("/max-sessions")
async def set_max_sessions(payload: MaxSessionsRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_max_sessions(s, payload.project_key, payload.max_sessions)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "max_sessions": payload.max_sessions}


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
            )
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"redispatch failed: {e}")
        await s.commit()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card not found")
    return res


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
            await apply_operation(s, op_type="delete", entity_type="card",
                project_key="", entity_id=card.id, payload={})
            count += 1
        await s.commit()
    return {"cleared": count}


@router.post("/cards/{cid}/resolve-impediment", response_model=CardResponse)
async def resolve_impediment(cid: str, payload: ImpedimentResolveRequest):
    """Resolve an impediment by dispatching to a specific agent."""
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
