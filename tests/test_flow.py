"""End-to-end happy path + key permission checks."""
from datetime import date, timedelta


from tests.conftest import register_buyer, register_seller


def test_full_vppa_flow(client):
    _, seller = register_seller(client)
    _, buyer = register_buyer(client)

    # RBAC: buyer cannot create plant
    plant_body = {"name": "P1", "capacity_kw": 100, "latitude": 18.5, "longitude": 73.8,
                  "ask_price_per_kwh": 5.0}
    assert client.post("/plants", json=plant_body, headers=buyer).status_code == 403

    plant = client.post("/plants", json=plant_body, headers=seller).json()
    assert plant["id"] in [p["id"] for p in client.get("/plants/marketplace", headers=buyer).json()]
    assert plant["id"] in [m["plant_id"] for m in client.get("/ml/matches", headers=buyer).json()]

    # contract: propose -> counter -> accept
    today = date.today()
    c = client.post("/contracts", headers=buyer, json={
        "plant_id": plant["id"], "price_per_kwh": 4.0, "monthly_kwh": 5000,
        "start_date": str(today - timedelta(days=5)), "end_date": str(today + timedelta(days=300))}).json()
    cid = c["id"]
    assert client.post(f"/contracts/{cid}/accept", headers=buyer).status_code == 409   # own offer
    assert client.post(f"/contracts/{cid}/negotiate", headers=seller,
                       json={"price_per_kwh": 4.5}).status_code == 200
    assert client.post(f"/contracts/{cid}/accept", headers=buyer).json()["status"] == "active"

    # readings auto-link to the active contract
    ts = f"{today - timedelta(days=2)}T10:00:00"
    items = [{"plant_id": plant["id"], "timestamp": f"{today - timedelta(days=2)}T{h:02d}:00:00",
              "kwh_generated": 80, "kwh_delivered": 70} for h in range(8, 12)]
    r = client.post("/readings/bulk", headers=seller, json={"items": items}).json()
    assert r == {"inserted": 4, "skipped_duplicates": 0}
    assert client.post("/readings", headers=seller, json=items[0]).status_code == 409
    assert ts

    # billing: 4 x 70 kWh x 4.5 = 1260
    inv = client.post("/billing/generate", headers=seller, json={
        "contract_id": cid, "period_start": str(today - timedelta(days=3)),
        "period_end": str(today - timedelta(days=1))})
    assert inv.status_code == 201, inv.text
    inv = inv.json()
    assert inv["kwh_billed"] == 280 and inv["total"] == 1260.0
    assert client.post("/billing/generate", headers=seller, json={      # no double billing
        "contract_id": cid, "period_start": str(today - timedelta(days=3)),
        "period_end": str(today - timedelta(days=1))}).status_code == 409

    pdf = client.get(f"/billing/invoices/{inv['id']}/pdf", headers=buyer)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # test-mode payment
    order = client.post(f"/billing/invoices/{inv['id']}/pay", headers=buyer).json()
    assert order["amount"] == 1260.0
    client.post("/billing/payments/confirm", headers=buyer,
                json={"gateway_order_id": order["gateway_order_id"], "outcome": "success"})
    assert client.get(f"/billing/invoices/{inv['id']}", headers=buyer).json()["status"] == "paid"


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
