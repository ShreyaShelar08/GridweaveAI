import os
import tempfile
import uuid

# Must be set BEFORE the app is imported.
# TEST_DATABASE_URL (e.g. an empty Postgres database that `alembic upgrade head` has been run on)
# is used if given; otherwise a throw-away SQLite file is used.
_test_url = os.environ.get("TEST_DATABASE_URL")
if _test_url:
    os.environ["DATABASE_URL"] = _test_url
    os.environ["AUTO_CREATE_TABLES"] = "false"
else:
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.mkdtemp(), 'test.db')}"
    os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["LOG_DIR"] = tempfile.mkdtemp()
os.environ["MAX_FAILED_LOGINS"] = "3"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASSWORD = "Passw0rd!x"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def unique_email(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}@test.com"


def seller_body(email=None, **over):
    body = {"email": email or unique_email("seller"), "full_name": "Test Seller", "password": PASSWORD,
            "phone": "9876543210", "company_name": "Sun Power Pvt Ltd", "city": "Pune"}
    return {**body, **over}


def buyer_body(email=None, **over):
    body = {"email": email or unique_email("buyer"), "full_name": "Test Buyer", "password": PASSWORD,
            "org_name": "Mall Co", "buyer_type": "mall", "annual_demand_kwh": 100000,
            "max_price_per_kwh": 4.8, "city": "Pune"}
    return {**body, **over}


def login(client, email, password=PASSWORD):
    return client.post("/auth/login", data={"username": email, "password": password})


def auth_header(client, email, password=PASSWORD):
    r = login(client, email, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def register_seller(client, **over):
    body = seller_body(**over)
    assert client.post("/auth/register/seller", json=body).status_code == 201
    return body["email"], auth_header(client, body["email"])


def register_buyer(client, **over):
    body = buyer_body(**over)
    assert client.post("/auth/register/buyer", json=body).status_code == 201
    return body["email"], auth_header(client, body["email"])
