"""Energy readings (what ingestion / simulators / meters push in).
Each reading is auto-linked to the plant's active contract so billing can sum it later."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Contract, ContractStatus, Plant, Reading, Role, User
from ..schemas import BulkResult, ReadingIn, ReadingOut, ReadingsBulk
from ..security import get_current_user, require_roles

router = APIRouter(prefix="/readings", tags=["Readings"])


def _check_plant(db: Session, plant_id: int, user: User) -> Plant:
    plant = db.get(Plant, plant_id)
    if not plant:
        raise HTTPException(404, f"Plant {plant_id} not found")
    if user.role != Role.admin and plant.owner_id != user.id:
        raise HTTPException(403, f"Plant {plant_id} is not yours")
    return plant


def _active_contract_id(db: Session, plant_id: int, ts: datetime) -> Optional[int]:
    return db.scalar(select(Contract.id).where(
        Contract.plant_id == plant_id, Contract.status == ContractStatus.active,
        Contract.start_date <= ts.date(), Contract.end_date >= ts.date()))


def _insert(db: Session, item: ReadingIn) -> Optional[Reading]:
    exists = db.scalar(select(Reading.id).where(Reading.plant_id == item.plant_id,
                                                Reading.timestamp == item.timestamp))
    if exists:
        return None
    reading = Reading(**item.model_dump(),
                      contract_id=_active_contract_id(db, item.plant_id, item.timestamp))
    db.add(reading)
    return reading


@router.post("", response_model=ReadingOut, status_code=201)
def add_reading(item: ReadingIn, user: User = Depends(require_roles(Role.seller, Role.admin)),
                db: Session = Depends(get_db)):
    _check_plant(db, item.plant_id, user)
    reading = _insert(db, item)
    if reading is None:
        raise HTTPException(409, "Reading for this plant and timestamp already exists")
    db.commit()
    db.refresh(reading)
    return reading


@router.post("/bulk", response_model=BulkResult, status_code=201)
def add_readings_bulk(data: ReadingsBulk, user: User = Depends(require_roles(Role.seller, Role.admin)),
                      db: Session = Depends(get_db)):
    for pid in {i.plant_id for i in data.items}:
        _check_plant(db, pid, user)
    inserted = skipped = 0
    seen = set()
    for item in data.items:
        key = (item.plant_id, item.timestamp)
        if key in seen or _insert(db, item) is None:
            skipped += 1
        else:
            inserted += 1
            db.flush()
        seen.add(key)
    db.commit()
    return BulkResult(inserted=inserted, skipped_duplicates=skipped)


@router.get("/plant/{plant_id}", response_model=list[ReadingOut])
def plant_readings(plant_id: int, start: Optional[datetime] = None, end: Optional[datetime] = None,
                   limit: int = 500, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plant = db.get(Plant, plant_id)
    if not plant:
        raise HTTPException(404, "Plant not found")
    if user.role == Role.buyer:
        raise HTTPException(403, "Buyers cannot view raw plant readings")
    if user.role == Role.seller and plant.owner_id != user.id:
        raise HTTPException(403, "Not your plant")
    q = select(Reading).where(Reading.plant_id == plant_id)
    if start:
        q = q.where(Reading.timestamp >= start)
    if end:
        q = q.where(Reading.timestamp <= end)
    return db.scalars(q.order_by(Reading.timestamp).limit(min(limit, 2000))).all()
