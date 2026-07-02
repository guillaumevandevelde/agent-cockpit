"""REST API for Web Push subscriptions and VAPID handshake."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.models.push_schemas import (
    PushSubscriptionIn,
    PushSubscriptionResponse,
    PushPreferencesUpdate,
    PushUnsubscribe,
    VapidPublicKeyResponse,
    PushTestResponse,
)
from app.services import push_service

router = APIRouter(prefix="/push", tags=["Push"])


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
async def vapid_public_key():
    """The application server key the browser needs to subscribe."""
    try:
        key = push_service.get_public_key()
    except Exception:
        return VapidPublicKeyResponse(public_key=None, configured=False)
    return VapidPublicKeyResponse(public_key=key, configured=bool(key))


@router.post("/subscribe", response_model=PushSubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def subscribe(payload: PushSubscriptionIn, db: AsyncSession = Depends(get_db)):
    sub = await push_service.save_subscription(db, payload)
    return PushSubscriptionResponse.model_validate(sub)


@router.patch("/preferences", response_model=PushSubscriptionResponse)
async def update_preferences(prefs: PushPreferencesUpdate, db: AsyncSession = Depends(get_db)):
    sub = await push_service.update_preferences(db, prefs)
    if sub is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Subscription not found")
    return PushSubscriptionResponse.model_validate(sub)


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(payload: PushUnsubscribe, db: AsyncSession = Depends(get_db)):
    await push_service.delete_subscription(db, payload.endpoint)
    return None


@router.post("/test", response_model=PushTestResponse)
async def send_test(db: AsyncSession = Depends(get_db)):
    sent = await push_service.send_test(db)
    return PushTestResponse(sent=sent)
