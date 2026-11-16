"""Payments: simulator, real-Razorpay flow (with the Razorpay HTTP API mocked), webhooks, reports."""
import hashlib
import hmac
import json
import uuid
from datetime import date, timedelta

import httpx
import pytest

from app.config import settings
from app.services import payments as gateways
from tests.conftest import register_buyer, register_seller

KEY_ID, KEY_SECRET, WH_SECRET = "rzp_test_abc123", "secret_key_xyz", "webhook_secret_123"


def make_invoice(client, kwh_per_hour=70.0):
    """seller + buyer + active contract @ Rs 4.50 + 4 readings + generated invoice."""
    _, seller = register_seller(client)
    _, buyer = register_buyer(client)
    plant = client.post("/plants", headers=seller, json={"name": "Plant X", "capacity_kw": 100, "latitude": 18.5,
                                                         "longitude": 73.8, "ask_price_per_kwh": 5.0}).json()
    today = date.today()
    c = client.post("/contracts", headers=buyer, json={
        "plant_id": plant["id"], "price_per_kwh": 4.5, "monthly_kwh": 5000,
        "start_date": str(today - timedelta(days=10)), "end_date": str(today + timedelta(days=300))}).json()
    assert client.post(f"/contracts/{c['id']}/accept", headers=seller).json()["status"] == "active"
    day = today - timedelta(days=2)
    items = [{"plant_id": plant["id"], "timestamp": f"{day}T{h:02d}:00:00", "kwh_generated": 80,
              "kwh_delivered": kwh_per_hour} for h in range(8, 12)]
    assert client.post("/readings/bulk", headers=seller, json={"items": items}).status_code == 201
    inv = client.post("/billing/generate", headers=seller, json={
        "contract_id": c["id"], "period_start": str(day), "period_end": str(day)})
    assert inv.status_code == 201, inv.text
    return seller, buyer, inv.json()


# ---------- simulator ----------
def test_simulator_payment_receipt_and_history(client):
    seller, buyer, inv = make_invoice(client)
    assert inv["total"] == 1260.0 and inv["due_date"] and inv["is_overdue"] is False
    order = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()
    assert order["provider"] == "test" and order["amount_paise"] == 126000
    # asking again reuses the open order (no second live order for one invoice)
    assert client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()["gateway_order_id"] == order["gateway_order_id"]
    # a failed simulated payment can be retried with a new order
    assert client.post("/billing/payments/confirm", headers=buyer,
                       json={"gateway_order_id": order["gateway_order_id"], "outcome": "failure"}).json()["status"] == "failed"
    order2 = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()
    assert order2["gateway_order_id"] != order["gateway_order_id"]
    paid = client.post("/billing/payments/confirm", headers=buyer,
                       json={"gateway_order_id": order2["gateway_order_id"], "outcome": "success"}).json()
    assert paid["status"] == "succeeded" and paid["receipt_number"].startswith("RCPT-")
    assert client.get(f"/billing/invoices/{inv['id']}", headers=buyer).json()["status"] == "paid"
    assert client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).status_code == 409
    # receipt + updated invoice PDF + history for both sides
    r = client.get(f"/billing/payments/{paid['id']}/receipt", headers=buyer)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert client.get(f"/billing/payments/{paid['id']}/receipt", headers=seller).status_code == 200
    assert client.get(f"/billing/invoices/{inv['id']}/pdf", headers=seller).content.startswith(b"%PDF")
    assert paid["id"] in [p["id"] for p in client.get("/billing/payments", headers=seller).json()]
    # only buyers pay
    assert client.post(f"/billing/invoices/{inv['id']}/pay", headers=seller).status_code == 403


def test_other_users_cannot_touch_payment(client):
    _, buyer, inv = make_invoice(client)
    _, outsider = register_buyer(client)
    order = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()
    assert client.post(f"/billing/invoices/{inv['id']}/pay", headers=outsider).status_code == 403
    assert client.post("/billing/payments/confirm", headers=outsider, json={
        "gateway_order_id": order["gateway_order_id"], "outcome": "success"}).status_code == 403
    assert client.get("/billing/payments", headers=outsider).json() == []


# ---------- real Razorpay flow with the Razorpay API mocked ----------
class FakeRazorpay:
    """Pretends to be api.razorpay.com so the whole flow is tested without internet or real keys."""

    def __init__(self):
        self.payments = {}
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        assert request.headers["authorization"].startswith("Basic ")   # key_id:key_secret
        path = request.url.path
        if request.method == "POST" and path == "/v1/orders":
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": f"order_{uuid.uuid4().hex[:14]}", "amount": body["amount"],
                                             "currency": "INR", "receipt": body["receipt"], "status": "created"})
        if request.method == "GET" and path.startswith("/v1/payments/"):
            return httpx.Response(200, json=self.payments[path.rsplit("/", 1)[1]])
        if request.method == "POST" and path.endswith("/capture"):
            pid = path.split("/")[-2]
            self.payments[pid]["status"] = "captured"
            return httpx.Response(200, json=self.payments[pid])
        return httpx.Response(404, json={})


@pytest.fixture()
def razorpay(monkeypatch):
    fake = FakeRazorpay()
    gw = gateways.RazorpayGateway(KEY_ID, KEY_SECRET, WH_SECRET, client=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(settings, "payment_mode", "razorpay")
    monkeypatch.setattr(gateways, "get_gateway", lambda: gw)
    return fake


def sign(order_id, payment_id):
    return hmac.new(KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def test_razorpay_checkout_verify_flow(client, razorpay):
    _, buyer, inv = make_invoice(client)
    order = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()
    assert order["provider"] == "razorpay" and order["key_id"] == KEY_ID and order["amount_paise"] == 126000
    oid = order["gateway_order_id"]
    razorpay.payments["pay_ABC123"] = {"id": "pay_ABC123", "order_id": oid, "amount": 126000, "status": "captured", "method": "upi"}

    # forged signature is rejected and the invoice stays unpaid
    bad = client.post("/billing/payments/verify", headers=buyer, json={
        "razorpay_order_id": oid, "razorpay_payment_id": "pay_ABC123", "razorpay_signature": "0" * 64})
    assert bad.status_code == 400
    assert client.get(f"/billing/invoices/{inv['id']}", headers=buyer).json()["status"] == "issued"

    good = client.post("/billing/payments/verify", headers=buyer, json={
        "razorpay_order_id": oid, "razorpay_payment_id": "pay_ABC123", "razorpay_signature": sign(oid, "pay_ABC123")})
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "succeeded" and good.json()["method"] == "upi"
    assert good.json()["provider_payment_id"] == "pay_ABC123"
    assert client.get(f"/billing/invoices/{inv['id']}", headers=buyer).json()["status"] == "paid"
    # calling verify again is harmless
    assert client.post("/billing/payments/verify", headers=buyer, json={
        "razorpay_order_id": oid, "razorpay_payment_id": "pay_ABC123",
        "razorpay_signature": sign(oid, "pay_ABC123")}).json()["status"] == "succeeded"
    # simulator endpoint is closed in razorpay mode
    assert client.post("/billing/payments/confirm", headers=buyer,
                       json={"gateway_order_id": oid, "outcome": "success"}).status_code == 400


def test_razorpay_amount_tampering_and_authorized_capture(client, razorpay):
    _, buyer, inv = make_invoice(client)
    oid = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()["gateway_order_id"]
    # gateway says only Rs 1 was paid -> refuse
    razorpay.payments["pay_LOW"] = {"id": "pay_LOW", "order_id": oid, "amount": 100, "status": "captured", "method": "card"}
    assert client.post("/billing/payments/verify", headers=buyer, json={
        "razorpay_order_id": oid, "razorpay_payment_id": "pay_LOW",
        "razorpay_signature": sign(oid, "pay_LOW")}).status_code == 400
    # 'authorized' payment gets captured by us
    razorpay.payments["pay_AUTH"] = {"id": "pay_AUTH", "order_id": oid, "amount": 126000, "status": "authorized", "method": "card"}
    ok = client.post("/billing/payments/verify", headers=buyer, json={
        "razorpay_order_id": oid, "razorpay_payment_id": "pay_AUTH", "razorpay_signature": sign(oid, "pay_AUTH")})
    assert ok.json()["status"] == "succeeded"
    assert ("POST", "/v1/payments/pay_AUTH/capture") in razorpay.calls


def webhook(client, event, entity, event_id):
    body = json.dumps({"event": event, "payload": {"payment": {"entity": entity}}}).encode()
    sig = hmac.new(WH_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"})


def test_webhook_marks_paid_and_is_idempotent(client, razorpay):
    _, buyer, inv = make_invoice(client)
    oid = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()["gateway_order_id"]
    # bad signature
    assert client.post("/webhooks/razorpay", content=b'{"event":"payment.captured"}',
                       headers={"X-Razorpay-Signature": "bad"}).status_code == 400
    # a failed attempt is recorded but the order stays open for a retry
    assert webhook(client, "payment.failed", {"id": "pay_F1", "order_id": oid, "amount": 126000,
                                              "error_description": "Card declined"}, "evt_fail_1").status_code == 200
    pay = client.get("/billing/payments", headers=buyer).json()[0]
    assert pay["attempts"] == 1 and pay["failure_reason"] == "Card declined" and pay["status"] == "created"
    # success arrives (buyer closed the browser; the webhook still completes the payment)
    ent = {"id": "pay_OK1", "order_id": oid, "amount": 126000, "method": "netbanking", "status": "captured"}
    assert webhook(client, "payment.captured", ent, "evt_ok_1").json() == {"status": "ok"}
    assert client.get(f"/billing/invoices/{inv['id']}", headers=buyer).json()["status"] == "paid"
    # Razorpay re-sends the same event: ignored
    assert webhook(client, "payment.captured", ent, "evt_ok_1").json() == {"status": "duplicate"}
    # a different event for an unknown order is acknowledged and ignored
    assert webhook(client, "payment.captured", {"id": "pay_X", "order_id": "order_unknown", "amount": 1},
                   "evt_other").json() == {"status": "ignored"}
    paid = [p for p in client.get("/billing/payments", headers=buyer).json() if p["status"] == "succeeded"]
    assert len(paid) == 1 and paid[0]["method"] == "netbanking"


def test_webhook_closed_in_simulator_mode(client):
    assert client.post("/webhooks/razorpay", content=b"{}", headers={"X-Razorpay-Signature": "x"}).status_code == 404


# ---------- reports ----------
def test_reports_scoped_and_correct(client):
    seller, buyer, inv = make_invoice(client)
    _, other_buyer = register_buyer(client)
    o = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()
    client.post("/billing/payments/confirm", headers=buyer, json={"gateway_order_id": o["gateway_order_id"], "outcome": "success"})

    s = client.get("/reports/summary", headers=seller).json()
    assert s["invoice_count"] == 1 and s["total_billed"] == 1260.0 and s["amount_paid"] == 1260.0
    assert s["amount_outstanding"] == 0 and s["collection_rate_percent"] == 100.0
    assert s["transactions"]["succeeded"] == 1
    assert client.get("/reports/summary", headers=other_buyer).json()["invoice_count"] == 0   # sees nothing of others

    m = client.get("/reports/monthly", headers=buyer).json()
    assert len(m) == 1 and m[0]["billed"] == 1260.0 and m[0]["kwh"] == 280.0

    csv_text = client.get("/reports/invoices.csv", headers=buyer).text
    assert csv_text.splitlines()[0].startswith("invoice_number,contract_id") and inv["invoice_number"] in csv_text
    assert "paid" in csv_text and "simulated" in csv_text
    tx = client.get("/reports/transactions.csv", headers=seller).text
    assert "pay_test_" in tx and "RCPT-" in tx
    assert inv["invoice_number"] not in client.get("/reports/invoices.csv", headers=other_buyer).text
    pdf = client.get("/reports/invoices.pdf", headers=seller)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # date filter excludes it
    far = str(date.today() + timedelta(days=400))
    assert client.get(f"/reports/summary?start={far}", headers=seller).json()["invoice_count"] == 0
    assert client.get("/reports/summary").status_code == 401


def test_unpaid_invoice_counts_as_outstanding(client):
    seller, _, inv = make_invoice(client)
    s = client.get("/reports/summary", headers=seller).json()
    assert s["amount_outstanding"] == 1260.0 and s["unpaid_count"] == 1 and s["amount_overdue"] == 0
