"""ML adapter layer.
These are PLACEHOLDER implementations with simple, explainable maths so the frontend
can be built now. Shreya replaces the function bodies with the real models (PV physics
model + forecasting + matching); function signatures and return shapes stay the same,
so the API routes and the frontend never change."""
import math
from datetime import datetime, timedelta, timezone
from statistics import median

from ..models import Buyer, Plant

PERFORMANCE_RATIO = 0.8
KWH_PER_KWP_YEAR = 1600  # rough India average yield


def forecast_generation(plant: Plant, hours: int) -> list[dict]:
    """Clear-sky bell curve between 06:00 and 18:00 local time (UTC+5:30)."""
    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    start = now.replace(minute=0, second=0, microsecond=0)
    out = []
    for i in range(hours):
        t = start + timedelta(hours=i)
        if 6 <= t.hour <= 18:
            kwh = plant.capacity_kw * PERFORMANCE_RATIO * math.sin(math.pi * (t.hour - 6) / 12)
        else:
            kwh = 0.0
        out.append({"timestamp": t.isoformat(), "predicted_kwh": round(max(kwh, 0), 3)})
    return out


def recommend_price(plant: Plant, buyers: list[Buyer]) -> dict:
    """Anchor on what buyers on the platform are willing to pay (median), clipped to a sane range."""
    demand_signal = median([b.max_price_per_kwh for b in buyers]) if buyers else 4.0
    price = min(max(demand_signal, 2.5), 7.0)
    return {
        "plant_id": plant.id,
        "recommended_price_per_kwh": round(price, 2),
        "range_low": round(price * 0.9, 2),
        "range_high": round(price * 1.1, 2),
        "basis": "median buyer max-price (placeholder model)",
    }


def match_plants(buyer: Buyer, plants: list[Plant]) -> list[dict]:
    """Score = 60% price fit + 40% volume fit. Returns best matches first."""
    results = []
    for p in plants:
        if p.ask_price_per_kwh <= buyer.max_price_per_kwh:
            price_fit = 1.0
        else:
            price_fit = max(0.0, 1 - (p.ask_price_per_kwh - buyer.max_price_per_kwh) / buyer.max_price_per_kwh)
        annual_gen = p.capacity_kw * KWH_PER_KWP_YEAR
        volume_fit = min(annual_gen, buyer.annual_demand_kwh) / max(annual_gen, buyer.annual_demand_kwh)
        results.append({
            "plant_id": p.id,
            "plant_name": p.name,
            "ask_price_per_kwh": p.ask_price_per_kwh,
            "score": round(0.6 * price_fit + 0.4 * volume_fit, 3),
        })
    return sorted(results, key=lambda r: r["score"], reverse=True)
