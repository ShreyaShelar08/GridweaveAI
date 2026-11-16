"""Razorpay webhook: the gateway tells US (server to server) about every payment result.
This is the reliable path - it still works if the buyer closes the browser right after paying.
Set the webhook URL in the Razorpay dashboard to  https://<your-domain>/webhooks/razorpay
and tick the events: payment.captured, payment.failed, order.paid."""
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Payment, WebhookEvent
from ..services import auth_service as audit_svc
from ..services import payment_service
from ..services import payments as gateways

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
logger = logging.getLogger("gridweave.webhooks")


@router.post("/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    gw = gateways.get_gateway()
    if gw.name != "razorpay":
        raise HTTPException(404, "Razorpay webhooks are not enabled")

    raw = await request.body()  # the signature is computed over the EXACT raw bytes
    if not gw.verify_webhook(raw, request.headers.get("X-Razorpay-Signature", "")):
        logger.warning("Webhook with invalid signature from %s", audit_svc.client_ip(request))
        raise HTTPException(400, "Invalid signature")

    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    event = body.get("event", "")
    event_id = request.headers.get("X-Razorpay-Event-Id") or f"{event}:{hashlib.sha256(raw).hexdigest()[:32]}"

    if db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider_event_id == event_id)):
        return {"status": "duplicate"}  # already processed - Razorpay retries, we must not double-apply
    db.add(WebhookEvent(provider_event_id=event_id, event_type=event, payload=raw.decode("utf-8", "replace")))

    entity = body.get("payload", {}).get("payment", {}).get("entity", {})
    payment = db.scalar(select(Payment).where(Payment.gateway_order_id == entity.get("order_id"))) \
        if entity.get("order_id") else None

    if payment is None:
        db.commit()
        return {"status": "ignored"}  # not one of our orders; answer 200 so Razorpay stops retrying

    if event in ("payment.captured", "order.paid"):
        if entity.get("amount") != gateways.to_paise(payment.amount):
            audit_svc.audit(db, "payment_mismatch", None, request, f"webhook payment={payment.id}")
        else:
            payment_service.mark_paid(db, payment, entity["id"], entity.get("method"), request)
    elif event == "payment.failed":
        payment_service.mark_attempt_failed(db, payment, entity.get("error_description") or "payment failed", request)
    db.commit()
    return {"status": "ok"}
