"""Payment gateways.
  * "test"     -> built-in simulator (no money, no internet) for demos and unit tests
  * "razorpay" -> the real Razorpay API. Use `rzp_test_...` keys while developing (no real money),
                  switch to `rzp_live_...` keys only when the business account is approved.
Routers only talk to the small interface below, so the gateway can be swapped without touching them."""
import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import HTTPException

from ..config import settings

logger = logging.getLogger("gridweave.payments")


def to_paise(amount_inr: float) -> int:
    """Razorpay works in paise (integer). Decimal avoids float rounding surprises."""
    return int((Decimal(str(amount_inr)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class GatewayOrder:
    order_id: str
    amount_paise: int
    currency: str = "INR"


class SimulatorGateway:
    name = "test"
    public_key = None

    def create_order(self, receipt: str, amount_inr: float) -> GatewayOrder:
        return GatewayOrder(f"order_test_{uuid.uuid4().hex[:14]}", to_paise(amount_inr))


class RazorpayGateway:
    name = "razorpay"
    API = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str = "",
                 client: httpx.Client | None = None):
        self.key_id, self.key_secret, self.webhook_secret = key_id, key_secret, webhook_secret
        self.client = client or httpx.Client(timeout=15)

    @property
    def public_key(self) -> str:
        return self.key_id  # the key id is public; the secret never leaves the server

    def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        try:
            resp = self.client.request(method, f"{self.API}{path}", json=json,
                                       auth=(self.key_id, self.key_secret))
        except httpx.HTTPError:
            logger.exception("Razorpay unreachable: %s %s", method, path)
            raise HTTPException(502, "Payment gateway is unreachable, please try again")
        if resp.status_code >= 400:
            logger.error("Razorpay error %s on %s %s: %s", resp.status_code, method, path, resp.text[:300])
            raise HTTPException(502, "Payment gateway rejected the request")
        return resp.json()

    def create_order(self, receipt: str, amount_inr: float) -> GatewayOrder:
        paise = to_paise(amount_inr)
        data = self._request("POST", "/orders", {"amount": paise, "currency": "INR", "receipt": receipt[:40]})
        return GatewayOrder(data["id"], paise, data.get("currency", "INR"))

    def verify_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        """Checkout returns signature = HMAC_SHA256(order_id + '|' + payment_id, key_secret)."""
        expected = hmac.new(self.key_secret.encode(), f"{order_id}|{payment_id}".encode(),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """Webhook header X-Razorpay-Signature = HMAC_SHA256(raw body, webhook secret)."""
        expected = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def fetch_payment(self, payment_id: str) -> dict:
        return self._request("GET", f"/payments/{payment_id}")

    def capture(self, payment_id: str, amount_paise: int) -> dict:
        return self._request("POST", f"/payments/{payment_id}/capture",
                             {"amount": amount_paise, "currency": "INR"})


_cached = None


def get_gateway():
    """Gateway chosen by PAYMENT_MODE in .env."""
    global _cached
    if _cached is None:
        if settings.payment_mode == "razorpay":
            _cached = RazorpayGateway(settings.razorpay_key_id, settings.razorpay_key_secret,
                                      settings.razorpay_webhook_secret)
        else:
            _cached = SimulatorGateway()
    return _cached
