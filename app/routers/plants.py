"""Seller plants + buyer profiles."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Buyer, Plant, Role, User
from ..schemas import BuyerOut, PlantCreate, PlantOut, PlantUpdate
from ..security import get_current_user, require_roles

plants_router = APIRouter(prefix="/plants", tags=["Plants"])
buyers_router = APIRouter(prefix="/buyers", tags=["Buyers"])


@plants_router.post("", response_model=PlantOut, status_code=201)
def register_plant(data: PlantCreate, user: User = Depends(require_roles(Role.seller)),
                   db: Session = Depends(get_db)):
    plant = Plant(owner_id=user.id, **data.model_dump())
    db.add(plant)
    db.commit()
    db.refresh(plant)
    return plant


@plants_router.get("/marketplace", response_model=list[PlantOut])
def marketplace(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """All listed plants - the marketplace view."""
    return db.scalars(select(Plant).where(Plant.status == "listed")).all()


@plants_router.get("/mine", response_model=list[PlantOut])
def my_plants(user: User = Depends(require_roles(Role.seller)), db: Session = Depends(get_db)):
    return db.scalars(select(Plant).where(Plant.owner_id == user.id)).all()


@plants_router.get("/{plant_id}", response_model=PlantOut)
def get_plant(plant_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    plant = db.get(Plant, plant_id)
    if not plant:
        raise HTTPException(404, "Plant not found")
    return plant


@plants_router.patch("/{plant_id}", response_model=PlantOut)
def update_plant(plant_id: int, data: PlantUpdate,
                 user: User = Depends(require_roles(Role.seller, Role.admin)),
                 db: Session = Depends(get_db)):
    plant = db.get(Plant, plant_id)
    if not plant:
        raise HTTPException(404, "Plant not found")
    if user.role != Role.admin and plant.owner_id != user.id:
        raise HTTPException(403, "Not your plant")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(plant, k, v)
    db.commit()
    db.refresh(plant)
    return plant


# ---------------- buyers ----------------
@buyers_router.get("/me", response_model=BuyerOut)
def my_buyer_profile(user: User = Depends(require_roles(Role.buyer)), db: Session = Depends(get_db)):
    if not user.buyer_profile:
        raise HTTPException(404, "Create your buyer profile first")
    return user.buyer_profile


@buyers_router.get("", response_model=list[BuyerOut])
def list_buyers(_: User = Depends(require_roles(Role.seller, Role.admin)), db: Session = Depends(get_db)):
    """Sellers can see demand on the platform."""
    return db.scalars(select(Buyer)).all()
