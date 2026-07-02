"""Pydantic schemas for the Web Push API."""
from pydantic import BaseModel, Field


class PushKeys(BaseModel):
    """The encryption keys a browser hands out with a subscription."""
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    """Mirrors the JSON shape of a browser `PushSubscription` plus mute prefs."""
    endpoint: str
    keys: PushKeys
    mute_input: bool = False
    mute_completion: bool = False
    mute_error: bool = False
    user_agent: str | None = None


class PushPreferencesUpdate(BaseModel):
    """Per-category muting, addressed by subscription endpoint."""
    endpoint: str
    mute_input: bool = False
    mute_completion: bool = False
    mute_error: bool = False


class PushUnsubscribe(BaseModel):
    endpoint: str


class PushSubscriptionResponse(BaseModel):
    endpoint: str
    mute_input: bool
    mute_completion: bool
    mute_error: bool

    model_config = {"from_attributes": True}


class VapidPublicKeyResponse(BaseModel):
    """The application server key the frontend passes to `pushManager.subscribe`."""
    public_key: str | None
    configured: bool


class PushTestResponse(BaseModel):
    sent: int = Field(description="Number of subscriptions the test reached.")
