"""Tests for the Web Push service: VAPID keys, event mapping, CRUD, delivery."""
import base64

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.push_subscription import PushSubscription
from app.models.push_schemas import PushSubscriptionIn, PushKeys, PushPreferencesUpdate
from app.models.schemas import PresenceSessionResponse
from app.services import push_service as svc


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _session(**overrides) -> PresenceSessionResponse:
    base = dict(
        session_id="sess-abcdef123456",
        label="myproj",
        project_path="/home/x/myproj",
        tmux_pane=None,
        status="active",
        status_text=None,
        last_narrative=None,
        last_narrative_at=None,
        modified_files=[],
        last_user_prompt=None,
        last_command=None,
        last_command_exit=None,
        activity_buckets=[0] * 30,
        total_events=1,
        error_count=0,
        started_at="2026-07-02T00:00:00+00:00",
        last_event_at="2026-07-02T00:00:00+00:00",
        ended_at=None,
    )
    base.update(overrides)
    return PresenceSessionResponse(**base)


def _sub(endpoint: str, **kw) -> PushSubscriptionIn:
    return PushSubscriptionIn(
        endpoint=endpoint,
        keys=PushKeys(p256dh="p256dh-key", auth="auth-key"),
        **kw,
    )


# --------------------------------------------------------------------------- #
# VAPID keys
# --------------------------------------------------------------------------- #

def test_generate_vapid_keys_shape():
    keys = svc._generate_vapid_keys()
    assert "-----BEGIN PRIVATE KEY-----" in keys["private_key"]
    # Application server key is a raw uncompressed P-256 point: 65 bytes.
    padded = keys["public_key"] + "=" * (-len(keys["public_key"]) % 4)
    raw = base64.urlsafe_b64decode(padded)
    assert len(raw) == 65
    assert raw[0] == 0x04


def test_get_vapid_keys_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_VAPID_FILE", tmp_path / "vapid.json")
    monkeypatch.setattr(svc, "_vapid_cache", None)
    monkeypatch.setattr(svc.settings, "vapid_public_key", None)
    monkeypatch.setattr(svc.settings, "vapid_private_key", None)
    first = svc.get_vapid_keys()
    second = svc.get_vapid_keys()
    assert first == second
    assert (tmp_path / "vapid.json").exists()


def test_config_keys_take_priority(monkeypatch):
    monkeypatch.setattr(svc.settings, "vapid_public_key", "CONFIG_PUB")
    monkeypatch.setattr(svc.settings, "vapid_private_key", "CONFIG_PRIV")
    assert svc.get_public_key() == "CONFIG_PUB"


# --------------------------------------------------------------------------- #
# Event → push mapping
# --------------------------------------------------------------------------- #

def test_stop_maps_to_input():
    push = svc.build_attention_push("Stop", _session(status_text="Waiting for input"))
    assert push is not None
    assert push.category == svc.CATEGORY_INPUT
    assert "myproj" in push.title
    assert push.url.startswith("/presence?session=")


def test_session_end_maps_to_completion():
    push = svc.build_attention_push("SessionEnd", _session())
    assert push.category == svc.CATEGORY_COMPLETION


def test_failed_command_maps_to_error():
    push = svc.build_attention_push(
        "PostToolUse", _session(last_command="pytest", last_command_exit=1)
    )
    assert push.category == svc.CATEGORY_ERROR
    assert "pytest" in push.body


def test_successful_command_no_push():
    assert svc.build_attention_push(
        "PostToolUse", _session(last_command="ls", last_command_exit=0)
    ) is None


def test_notification_without_narrative_no_push():
    assert svc.build_attention_push("Notification", _session()) is None


def test_notification_with_narrative_maps_to_input():
    push = svc.build_attention_push(
        "Notification", _session(last_narrative="Grant permission?")
    )
    assert push.category == svc.CATEGORY_INPUT
    assert push.body == "Grant permission?"


def test_tmux_pane_targets_cc_bridge():
    push = svc.build_attention_push("Stop", _session(tmux_pane="%3"))
    assert push.url.startswith("/cc-bridge?attach=")


def test_unrelated_event_no_push():
    assert svc.build_attention_push("PreToolUse", _session()) is None


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_save_subscription_is_idempotent(db):
    await svc.save_subscription(db, _sub("https://push/1"))
    await svc.save_subscription(db, _sub("https://push/1", mute_error=True))
    subs = (await svc._subscriptions_for_category(db, svc.CATEGORY_INPUT))
    assert len(subs) == 1
    assert subs[0].mute_error is True


@pytest.mark.asyncio
async def test_update_preferences(db):
    await svc.save_subscription(db, _sub("https://push/1"))
    updated = await svc.update_preferences(
        db, PushPreferencesUpdate(endpoint="https://push/1", mute_completion=True)
    )
    assert updated.mute_completion is True


@pytest.mark.asyncio
async def test_update_preferences_missing_returns_none(db):
    assert await svc.update_preferences(
        db, PushPreferencesUpdate(endpoint="nope")
    ) is None


@pytest.mark.asyncio
async def test_delete_subscription(db):
    await svc.save_subscription(db, _sub("https://push/1"))
    assert await svc.delete_subscription(db, "https://push/1") is True
    assert await svc.delete_subscription(db, "https://push/1") is False


@pytest.mark.asyncio
async def test_muting_filters_category(db):
    await svc.save_subscription(db, _sub("https://push/muted", mute_input=True))
    await svc.save_subscription(db, _sub("https://push/open"))
    input_subs = await svc._subscriptions_for_category(db, svc.CATEGORY_INPUT)
    completion_subs = await svc._subscriptions_for_category(db, svc.CATEGORY_COMPLETION)
    assert {s.endpoint for s in input_subs} == {"https://push/open"}
    assert len(completion_subs) == 2


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_send_attention_delivers_to_non_muted(db, monkeypatch):
    sent_to = []
    monkeypatch.setattr(svc, "_webpush_sync", lambda sub, payload: sent_to.append(sub.endpoint))
    await svc.save_subscription(db, _sub("https://push/a"))
    await svc.save_subscription(db, _sub("https://push/b", mute_input=True))
    count = await svc.send_attention(db, "Stop", _session())
    assert count == 1
    assert sent_to == ["https://push/a"]


@pytest.mark.asyncio
async def test_send_attention_none_event_sends_nothing(db, monkeypatch):
    monkeypatch.setattr(svc, "_webpush_sync", lambda sub, payload: None)
    await svc.save_subscription(db, _sub("https://push/a"))
    assert await svc.send_attention(db, "PreToolUse", _session()) == 0


@pytest.mark.asyncio
async def test_expired_subscription_is_pruned(db, monkeypatch):
    class _Resp:
        status_code = 410

    class _Gone(Exception):
        response = _Resp()

    def boom(sub, payload):
        raise _Gone()

    monkeypatch.setattr(svc, "_webpush_sync", boom)
    await svc.save_subscription(db, _sub("https://push/gone"))
    count = await svc.send_attention(db, "Stop", _session())
    assert count == 0
    remaining = await svc._subscriptions_for_category(db, svc.CATEGORY_INPUT)
    assert remaining == []


@pytest.mark.asyncio
async def test_transient_failure_keeps_subscription(db, monkeypatch):
    def boom(sub, payload):
        raise RuntimeError("network blip")

    monkeypatch.setattr(svc, "_webpush_sync", boom)
    await svc.save_subscription(db, _sub("https://push/keep"))
    count = await svc.send_attention(db, "Stop", _session())
    assert count == 0
    remaining = await svc._subscriptions_for_category(db, svc.CATEGORY_INPUT)
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_send_test_ignores_muting(db, monkeypatch):
    hits = []
    monkeypatch.setattr(svc, "_webpush_sync", lambda sub, payload: hits.append(payload))
    await svc.save_subscription(
        db, _sub("https://push/x", mute_input=True, mute_completion=True, mute_error=True)
    )
    count = await svc.send_test(db)
    assert count == 1
    assert hits[0]["category"] == "test"
