"""ML models exposed as APIs for the frontend. Currently backed by placeholders in
services/ml_client.py until Shreya's real models are plugged in."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Buyer, Plant, Role, User
from ..security import get_current_user, require_roles
from ..services import ml_client

router = APIRouter(prefix="/ml", tags=["ML"])


def _plant(db, plant_id):
    plant = db.get(Plant, plant_id)
    if not plant:
        raise HTTPException(404, "Plant not found")
    return plant


@router.get("/forecast/{plant_id}")
def forecast(plant_id: int, hours: int = Query(24, ge=1, le=168),
             db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    plant = _plant(db, plant_id)
    return {"plant_id": plant.id, "model": "placeholder-clear-sky-v0",
            "forecast": ml_client.forecast_generation(plant, hours)}


@router.get("/price-recommendation/{plant_id}")
def price_recommendation(plant_id: int, db: Session = Depends(get_db),
                         _: User = Depends(require_roles(Role.seller, Role.admin))):
    plant = _plant(db, plant_id)
    return ml_client.recommend_price(plant, db.scalars(select(Buyer)).all())


@router.get("/matches")
def matches(user: User = Depends(require_roles(Role.buyer)), db: Session = Depends(get_db)):
    """Best-matching plants for the logged-in buyer."""
    if not user.buyer_profile:
        raise HTTPException(400, "Create your buyer profile first")
    plants = db.scalars(select(Plant).where(Plant.status == "listed")).all()
    return ml_client.match_plants(user.buyer_profile, plants)
