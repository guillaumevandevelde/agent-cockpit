"""Agent Mail service — inter-agent messaging, outside the op-log.

Direct SQLAlchemy CRUD in the style of service.py (no apply_operation). Identity
is the durable per-repo role (project_key, handle); a fresh developer session
inherits the developer mailbox, so async handoffs survive session churn.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.kanban.models import AgentIdentity, AgentMessage
from app.kanban.schemas import MESSAGE_KINDS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- identities -------------------------------------------------------------


async def ensure_identity(session, project_key: str, handle: str, *,
                          display_name: str | None = None,
                          agent_session: str | None = None) -> AgentIdentity:
    """Upsert the durable (project_key, handle) identity, refreshing its live
    session and last-seen timestamp."""
    stmt = (
        select(AgentIdentity)
        .where(AgentIdentity.project_key == project_key)
        .where(AgentIdentity.handle == handle)
    )
    identity = (await session.execute(stmt)).scalar_one_or_none()
    now = _utcnow()
    if identity is None:
        identity = AgentIdentity(
            id=uuid.uuid4().hex, project_key=project_key, handle=handle,
            display_name=display_name, last_session=agent_session,
            created_at=now, last_seen_at=now,
        )
        session.add(identity)
    else:
        if display_name is not None:
            identity.display_name = display_name
        if agent_session is not None:
            identity.last_session = agent_session
        identity.last_seen_at = now
    await session.flush()
    return identity


async def list_identities(session, project_key: str) -> list[AgentIdentity]:
    stmt = (
        select(AgentIdentity)
        .where(AgentIdentity.project_key == project_key)
        .order_by(AgentIdentity.handle.asc())
    )
    return (await session.execute(stmt)).scalars().all()


# --- messages ---------------------------------------------------------------


async def send_message(session, project_key: str, from_handle: str,
                       to_handle: str | None, kind: str, subject: str, body: str,
                       *, card_id: str | None = None,
                       in_reply_to: str | None = None) -> AgentMessage:
    """Persist a message. `to_handle=None` is a broadcast. A context_response that
    references a request marks that request `answered`."""
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"unknown message kind: {kind}")
    msg = AgentMessage(
        id=uuid.uuid4().hex, project_key=project_key, from_handle=from_handle,
        to_handle=to_handle, kind=kind, subject=subject, body=body,
        card_id=card_id, in_reply_to=in_reply_to, status="unread",
        created_at=_utcnow(),
    )
    session.add(msg)
    if in_reply_to is not None:
        parent = await session.get(AgentMessage, in_reply_to)
        if parent is not None and parent.kind == "context_request":
            parent.status = "answered"
    await session.flush()
    return msg


async def list_inbox(session, project_key: str, handle: str, *,
                     unread_only: bool = False,
                     include_broadcast: bool = True) -> list[AgentMessage]:
    recipient = AgentMessage.to_handle == handle
    target = or_(recipient, AgentMessage.to_handle.is_(None)) if include_broadcast else recipient
    stmt = (
        select(AgentMessage)
        .where(AgentMessage.project_key == project_key)
        .where(target)
        .order_by(AgentMessage.created_at.desc())
    )
    if unread_only:
        stmt = stmt.where(AgentMessage.status == "unread")
    return (await session.execute(stmt)).scalars().all()


async def list_sent(session, project_key: str, handle: str) -> list[AgentMessage]:
    stmt = (
        select(AgentMessage)
        .where(AgentMessage.project_key == project_key)
        .where(AgentMessage.from_handle == handle)
        .order_by(AgentMessage.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


async def list_for_card(session, project_key: str, card_id: str) -> list[AgentMessage]:
    stmt = (
        select(AgentMessage)
        .where(AgentMessage.project_key == project_key)
        .where(AgentMessage.card_id == card_id)
        .order_by(AgentMessage.created_at.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def list_thread(session, root_message_id: str) -> list[AgentMessage]:
    """The root message plus everything replying to it, oldest first."""
    stmt = (
        select(AgentMessage)
        .where(or_(AgentMessage.id == root_message_id,
                   AgentMessage.in_reply_to == root_message_id))
        .order_by(AgentMessage.created_at.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def get_message(session, message_id: str) -> AgentMessage | None:
    return await session.get(AgentMessage, message_id)


async def mark_read(session, message_id: str, reader_handle: str) -> AgentMessage | None:
    """Mark a message read when the reader is its recipient. Broadcasts (no
    recipient) can be read by anyone. Already-answered messages keep that status."""
    msg = await session.get(AgentMessage, message_id)
    if msg is None:
        return None
    is_recipient = msg.to_handle is None or msg.to_handle == reader_handle
    if is_recipient and msg.status == "unread":
        msg.status = "read"
        msg.read_at = _utcnow()
        await session.flush()
    return msg


async def pending_for_card(session, project_key: str, card_id: str,
                           handle: str) -> list[AgentMessage]:
    """Unread handoff/context_request messages addressed to `handle` for a card —
    used to warm-start the dispatch prompt."""
    stmt = (
        select(AgentMessage)
        .where(AgentMessage.project_key == project_key)
        .where(AgentMessage.card_id == card_id)
        .where(AgentMessage.status == "unread")
        .where(AgentMessage.kind.in_(["handoff", "context_request"]))
        .where(or_(AgentMessage.to_handle == handle, AgentMessage.to_handle.is_(None)))
        .order_by(AgentMessage.created_at.asc())
    )
    return (await session.execute(stmt)).scalars().all()
