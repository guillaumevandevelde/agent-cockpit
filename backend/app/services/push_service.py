"""Web Push (VAPID) service: key management, subscriptions, and sending.

Turns the presence attention-events into real OS push notifications so a closed
tab or a phone still buzzes. VAPID keys are read from config when set, otherwise
generated once and cached beside the other ~/.claude-registry state so push works
out of the box in a single-host setup.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.push_subscription import PushSubscription
from app.models.push_schemas import PushSubscriptionIn, PushPreferencesUpdate
from app.models.schemas import PresenceSessionResponse

logger = logging.getLogger(__name__)

# Attention categories, matching the per-device mute switches.
CATEGORY_INPUT = "input"
CATEGORY_COMPLETION = "completion"
CATEGORY_ERROR = "error"

_MUTE_COLUMN = {
    CATEGORY_INPUT: PushSubscription.mute_input,
    CATEGORY_COMPLETION: PushSubscription.mute_completion,
    CATEGORY_ERROR: PushSubscription.mute_error,
}

_VAPID_FILE = Path.home() / ".claude-registry" / "vapid.json"
_vapid_cache: Optional[dict] = None


@dataclass
class AttentionPush:
    """A resolved notification ready to be delivered to subscribers."""
    category: str
    title: str
    body: str
    url: str
    tag: str

    def to_payload(self) -> dict:
        return {
            "category": self.category,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "tag": self.tag,
        }


# --------------------------------------------------------------------------- #
# VAPID key management
# --------------------------------------------------------------------------- #

def _generate_vapid_keys() -> dict:
    """Generate a P-256 keypair: base64url application server key + PKCS8 PEM."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    raw_public = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {"public_key": public_b64, "private_key": private_pem}


def get_vapid_keys() -> dict:
    """Return the active VAPID keypair, generating+caching one if unconfigured.

    Priority: explicit config > cached file > freshly generated (persisted).
    """
    global _vapid_cache
    if settings.vapid_public_key and settings.vapid_private_key:
        return {
            "public_key": settings.vapid_public_key,
            "private_key": settings.vapid_private_key,
        }
    if _vapid_cache is not None:
        return _vapid_cache

    if _VAPID_FILE.exists():
        try:
            data = json.loads(_VAPID_FILE.read_text(encoding="utf-8"))
            if data.get("public_key") and data.get("private_key"):
                _vapid_cache = data
                return data
        except (OSError, ValueError):
            logger.warning("Corrupt VAPID cache at %s; regenerating", _VAPID_FILE)

    keys = _generate_vapid_keys()
    try:
        _VAPID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _VAPID_FILE.write_text(json.dumps(keys), encoding="utf-8")
    except OSError:
        logger.warning("Could not persist VAPID keys to %s", _VAPID_FILE)
    _vapid_cache = keys
    return keys


def get_public_key() -> str:
    return get_vapid_keys()["public_key"]


# --------------------------------------------------------------------------- #
# Attention → notification mapping (pure)
# --------------------------------------------------------------------------- #

def _label(session: PresenceSessionResponse) -> str:
    return session.label or session.session_id[:8]


def _target_url(session: PresenceSessionResponse) -> str:
    if session.tmux_pane:
        return f"/cc-bridge?attach={quote(session.tmux_pane, safe='')}"
    return f"/presence?session={quote(session.session_id, safe='')}"


def build_attention_push(
    event_type: str, session: PresenceSessionResponse
) -> Optional[AttentionPush]:
    """Map a presence hook event to a push, or None when it needs no attention."""
    label = _label(session)
    url = _target_url(session)
    sid = session.session_id

    if event_type == "Stop":
        return AttentionPush(
            category=CATEGORY_INPUT,
            title=f"🟡 {label} wacht op je input",
            body=session.status_text or "Waiting for input",
            url=url,
            tag=f"{sid}:input",
        )

    if event_type == "Notification" and session.last_narrative:
        return AttentionPush(
            category=CATEGORY_INPUT,
            title=f"🔐 {label}",
            body=session.last_narrative,
            url=url,
            tag=f"{sid}:note",
        )

    if event_type == "SessionEnd":
        return AttentionPush(
            category=CATEGORY_COMPLETION,
            title=f"✅ {label} — sessie klaar",
            body=session.status_text or "Claude is klaar",
            url=url,
            tag=f"{sid}:done",
        )

    if event_type == "PostToolUse" and session.last_command_exit not in (None, 0):
        return AttentionPush(
            category=CATEGORY_ERROR,
            title=f"🔴 {label}: commando faalde",
            body=f"$ {session.last_command}" if session.last_command else "Een commando faalde",
            url=url,
            tag=f"{sid}:error",
        )

    return None


# --------------------------------------------------------------------------- #
# Subscription CRUD
# --------------------------------------------------------------------------- #

async def save_subscription(db: AsyncSession, payload: PushSubscriptionIn) -> PushSubscription:
    """Upsert by endpoint so re-subscribing the same browser is idempotent."""
    existing = (await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )).scalar_one_or_none()
    if existing is None:
        existing = PushSubscription(endpoint=payload.endpoint)
        db.add(existing)
    existing.p256dh = payload.keys.p256dh
    existing.auth = payload.keys.auth
    existing.mute_input = payload.mute_input
    existing.mute_completion = payload.mute_completion
    existing.mute_error = payload.mute_error
    existing.user_agent = payload.user_agent
    await db.flush()
    return existing


async def update_preferences(
    db: AsyncSession, prefs: PushPreferencesUpdate
) -> Optional[PushSubscription]:
    sub = (await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == prefs.endpoint)
    )).scalar_one_or_none()
    if sub is None:
        return None
    sub.mute_input = prefs.mute_input
    sub.mute_completion = prefs.mute_completion
    sub.mute_error = prefs.mute_error
    await db.flush()
    return sub


async def delete_subscription(db: AsyncSession, endpoint: str) -> bool:
    sub = (await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    )).scalar_one_or_none()
    if sub is None:
        return False
    await db.delete(sub)
    await db.flush()
    return True


async def _subscriptions_for_category(db: AsyncSession, category: str) -> list[PushSubscription]:
    mute_col = _MUTE_COLUMN[category]
    result = await db.execute(
        select(PushSubscription).where(or_(mute_col.is_(False), mute_col.is_(None)))
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #

def _webpush_sync(sub: PushSubscription, payload: dict) -> None:
    """Blocking send via pywebpush. Raises WebPushException on delivery failure."""
    from pywebpush import webpush

    keys = get_vapid_keys()
    webpush(
        subscription_info={
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        },
        data=json.dumps(payload),
        vapid_private_key=keys["private_key"],
        vapid_claims={"sub": settings.vapid_subject},
        timeout=10,
    )


def _is_expired(exc: Exception) -> bool:
    """404/410 from the push service means the subscription is gone for good."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) in (404, 410)


async def _deliver(db: AsyncSession, subs: list[PushSubscription], payload: dict) -> int:
    sent = 0
    expired: list[PushSubscription] = []
    for sub in subs:
        try:
            await asyncio.to_thread(_webpush_sync, sub, payload)
            sent += 1
        except Exception as exc:  # pywebpush raises WebPushException / requests errors
            if _is_expired(exc):
                expired.append(sub)
            else:
                logger.warning("Push to %s failed: %s", sub.endpoint[:40], exc)
    for sub in expired:
        await db.delete(sub)
    if expired:
        await db.flush()
    return sent


async def send_attention(
    db: AsyncSession, event_type: str, session: PresenceSessionResponse
) -> int:
    """Build a push for this event and deliver it to all non-muted subscribers."""
    push = build_attention_push(event_type, session)
    if push is None:
        return 0
    subs = await _subscriptions_for_category(db, push.category)
    if not subs:
        return 0
    return await _deliver(db, subs, push.to_payload())


async def send_test(db: AsyncSession) -> int:
    """Deliver a canned notification to every subscription (ignores muting)."""
    result = await db.execute(select(PushSubscription))
    subs = list(result.scalars().all())
    if not subs:
        return 0
    payload = {
        "category": "test",
        "title": "🔔 Cockpit test",
        "body": "Push-notificaties werken.",
        "url": "/presence",
        "tag": "cockpit:test",
    }
    return await _deliver(db, subs, payload)


async def dispatch_attention_bg(event_type: str, session: PresenceSessionResponse) -> None:
    """Fire-and-forget wrapper: own DB session, never propagates errors."""
    try:
        async with AsyncSessionLocal() as db:
            await send_attention(db, event_type, session)
            await db.commit()
    except Exception:
        logger.exception("Background push dispatch failed")


# Strong references so detached dispatch tasks aren't garbage-collected mid-flight.
_bg_tasks: set = set()


def schedule_dispatch(event_type: str, session: PresenceSessionResponse) -> None:
    """Kick off a non-blocking push dispatch for this presence event."""
    task = asyncio.create_task(dispatch_attention_bg(event_type, session))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
