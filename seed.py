"""Demo data for development ONLY:  python seed.py
Creates admin / seller / buyer accounts, a plant, an active contract and 7 days of readings."""
import math
from datetime import date, datetime, timedelta

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import (Buyer, Contract, ContractStatus, Plant, Reading, Role, SellerProfile, User, utcnow)
from app.security import hash_password

if settings.environment == "production":
    raise SystemExit("seed.py is for development only - refusing to run in production.")
if settings.auto_create_tables:
    Base.metadata.create_all(bind=engine)  # otherwise run `alembic upgrade head` first
db = SessionLocal()

if db.query(User).count():
    print("Database already has data - skipping seed.")
    raise SystemExit

admin = User(email="admin@gridweave.ai", full_name="Admin", role=Role.admin,
             hashed_password=hash_password("Admin@12345"))
seller = User(email="seller@gridweave.ai", full_name="Sunil Solar", role=Role.seller,
              hashed_password=hash_password("Seller@12345"))
buyer_u = User(email="buyer@gridweave.ai", full_name="Factory Buyer", role=Role.buyer,
               hashed_password=hash_password("Buyer@12345"))
seller.seller_profile = SellerProfile(company_name="Sunil Solar Pvt Ltd", city="Pune")
db.add_all([admin, seller, buyer_u])
db.flush()

plant = Plant(owner_id=seller.id, name="Pune Rooftop Plant", capacity_kw=500, latitude=18.52,
              longitude=73.86, city="Pune", ask_price_per_kwh=4.5)
buyer = Buyer(user_id=buyer_u.id, org_name="Bhosari Auto Parts", buyer_type="factory",
              annual_demand_kwh=600_000, max_price_per_kwh=5.0, city="Pune")
db.add_all([plant, buyer])
db.flush()

today = date.today()
contract = Contract(plant_id=plant.id, buyer_id=buyer.id, price_per_kwh=4.5, monthly_kwh=40_000,
                    start_date=today - timedelta(days=10), end_date=today + timedelta(days=355),
                    status=ContractStatus.active, last_offer_by="buyer", activated_at=utcnow())
db.add(contract)
db.flush()

for d in range(7, 0, -1):
    day = datetime.combine(today - timedelta(days=d), datetime.min.time())
    for h in range(6, 19):
        gen = round(500 * 0.8 * math.sin(math.pi * (h - 6) / 12), 2)
        db.add(Reading(plant_id=plant.id, contract_id=contract.id, timestamp=day + timedelta(hours=h),
                       kwh_generated=gen, kwh_delivered=round(gen * 0.95, 2), source="simulator"))
db.commit()
print("Seeded. Logins:\n  admin@gridweave.ai / Admin@12345\n  seller@gridweave.ai / Seller@12345\n"
      "  buyer@gridweave.ai / Buyer@12345")
