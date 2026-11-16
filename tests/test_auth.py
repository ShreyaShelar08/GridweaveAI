"""Authentication, sessions, lockout and role-based access."""
from datetime import date, timedelta

from tests.conftest import (PASSWORD, auth_header, buyer_body, login, register_buyer, register_seller,
                            seller_body, unique_email)


def test_register_creates_role_profiles(client):
    email, hdr = register_seller(client)
    me = client.get("/auth/me", headers=hdr).json()
    assert me["role"] == "seller" and me["seller_profile"]["company_name"] == "Sun Power Pvt Ltd"
    assert me["buyer_profile"] is None
    email, hdr = register_buyer(client)
    me = client.get("/auth/me", headers=hdr).json()
    assert me["role"] == "buyer" and me["buyer_profile"]["org_name"] == "Mall Co"
    assert "hashed_password" not in me


def test_register_validation(client):
    body = seller_body()
    assert client.post("/auth/register/seller", json={**body, "password": "short"}).status_code == 422
    assert client.post("/auth/register/seller", json={**body, "password": "alllowercase1"}).status_code == 422
    assert client.post("/auth/register/seller", json={**body, "phone": "abc"}).status_code == 422
    assert client.post("/auth/register/buyer", json=buyer_body(buyer_type="spaceship")).status_code == 422
    # no way to become admin through the API
    assert client.post("/auth/register/seller", json={**body, "role": "admin"}).status_code == 201
    assert client.get("/auth/me", headers=auth_header(client, body["email"])).json()["role"] == "seller"
    # duplicate email (case-insensitive)
    assert client.post("/auth/register/seller", json={**body, "email": body["email"].upper()}).status_code == 409


def test_login_and_protected_routes(client):
    email, _ = register_seller(client)
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert login(client, email, "Wrong-Pass1").status_code == 401
    assert login(client, "nobody@test.com").status_code == 401
    pair = login(client, email).json()
    assert pair["token_type"] == "bearer" and pair["expires_in"] > 0 and pair["refresh_token"]
    # a refresh token is not an access token
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {pair['refresh_token']}"}).status_code == 401


def test_lockout_after_failed_logins(client):
    email, _ = register_buyer(client)
    for _ in range(3):  # MAX_FAILED_LOGINS=3 in tests
        assert login(client, email, "Wrong-Pass1").status_code == 401
    assert login(client, email).status_code == 429  # even the right password is refused while locked


def test_refresh_rotation_and_reuse_detection(client):
    email, _ = register_seller(client)
    first = login(client, email).json()
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    second = second.json()
    assert second["refresh_token"] != first["refresh_token"]
    # old refresh token replayed -> rejected AND the whole session is revoked
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_logout_revokes_refresh_token(client):
    email, _ = register_seller(client)
    pair = login(client, email).json()
    assert client.post("/auth/logout", json={"refresh_token": pair["refresh_token"]}).status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401


def test_change_password_kills_old_sessions(client):
    email, old_hdr = register_buyer(client)
    pair = login(client, email).json()
    bad = client.post("/auth/change-password", headers=old_hdr,
                      json={"current_password": "Nope-Pass1", "new_password": "NewPassw0rd!"})
    assert bad.status_code == 400
    ok = client.post("/auth/change-password", headers=old_hdr,
                     json={"current_password": PASSWORD, "new_password": "NewPassw0rd!"})
    assert ok.status_code == 204
    assert client.get("/auth/me", headers=old_hdr).status_code == 401                       # old access token dead
    assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401
    assert login(client, email, PASSWORD).status_code == 401
    assert login(client, email, "NewPassw0rd!").status_code == 200


def test_logout_all(client):
    email, hdr = register_seller(client)
    assert client.post("/auth/logout-all", headers=hdr).status_code == 204
    assert client.get("/auth/me", headers=hdr).status_code == 401


def test_role_based_access(client):
    _, seller = register_seller(client)
    _, buyer = register_buyer(client)
    plant = {"name": "Plant A", "capacity_kw": 50, "latitude": 18.5, "longitude": 73.8, "ask_price_per_kwh": 4.0}
    assert client.post("/plants", json=plant, headers=buyer).status_code == 403
    pid = client.post("/plants", json=plant, headers=seller).json()["id"]
    today = date.today()
    contract = {"plant_id": pid, "price_per_kwh": 4.0, "monthly_kwh": 1000,
                "start_date": str(today), "end_date": str(today + timedelta(days=30))}
    assert client.post("/contracts", json=contract, headers=seller).status_code == 403   # sellers can't propose
    assert client.get("/ml/matches", headers=seller).status_code == 403
    assert client.get("/admin/users", headers=seller).status_code == 403
    assert client.get("/admin/users", headers=buyer).status_code == 403
    assert client.get("/admin/audit-log", headers=buyer).status_code == 403
    # profile endpoints are role specific
    assert client.patch("/profile/seller", json={"city": "Nashik"}, headers=seller).json()["seller_profile"]["city"] == "Nashik"
    assert client.patch("/profile/seller", json={"city": "X"}, headers=buyer).status_code == 403
    assert client.patch("/profile/buyer", json={"max_price_per_kwh": 5.5}, headers=buyer).json()["buyer_profile"]["max_price_per_kwh"] == 5.5
    assert client.patch("/profile/buyer", json={"city": "X"}, headers=seller).status_code == 403


def test_another_sellers_data_is_private(client):
    _, s1 = register_seller(client)
    _, s2 = register_seller(client)
    plant = {"name": "Plant A", "capacity_kw": 50, "latitude": 18.5, "longitude": 73.8, "ask_price_per_kwh": 4.0}
    pid = client.post("/plants", json=plant, headers=s1).json()["id"]
    assert client.patch(f"/plants/{pid}", json={"ask_price_per_kwh": 1.0}, headers=s2).status_code == 403
    assert client.get(f"/readings/plant/{pid}", headers=s2).status_code == 403


def test_audit_log_records_events(client):
    from app.database import SessionLocal
    from app.models import AuditLog, Role, User
    from app.security import hash_password
    email = unique_email("admin")
    with SessionLocal() as db:
        db.add(User(email=email, full_name="Admin", role=Role.admin, hashed_password=hash_password(PASSWORD)))
        db.commit()
    admin = auth_header(client, email)
    seller_email, _ = register_seller(client)
    login(client, seller_email, "Wrong-Pass1")
    actions = {a["action"] for a in client.get("/admin/audit-log?limit=200", headers=admin).json()}
    assert {"register", "login", "login_failed"} <= actions
    assert client.get("/admin/stats", headers=admin).status_code == 200
    with SessionLocal() as db:
        assert db.query(AuditLog).count() > 0
