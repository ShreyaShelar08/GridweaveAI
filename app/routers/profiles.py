"""Role-specific profile editing: sellers and buyers each have their own endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Role, SellerProfile, User
from ..schemas import BuyerUpdate, MeOut, SellerUpdate
from ..security import require_roles

router = APIRouter(prefix="/profile", tags=["Profile"])
USER_FIELDS = {"full_name", "phone"}


def _apply(user: User, profile, changes: dict) -> None:
    for key, value in changes.items():
        setattr(user if key in USER_FIELDS else profile, key, value)


@router.patch("/seller", response_model=MeOut)
def update_seller(data: SellerUpdate, user: User = Depends(require_roles(Role.seller)),
                  db: Session = Depends(get_db)):
    if user.seller_profile is None:
        user.seller_profile = SellerProfile(company_name=data.company_name or user.full_name)
    _apply(user, user.seller_profile, data.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(user)
    return user


@router.patch("/buyer", response_model=MeOut)
def update_buyer(data: BuyerUpdate, user: User = Depends(require_roles(Role.buyer)),
                 db: Session = Depends(get_db)):
    if user.buyer_profile is None:
        raise HTTPException(404, "Buyer profile missing")
    _apply(user, user.buyer_profile, data.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(user)
    return user
