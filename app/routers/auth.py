"""Registration (separate for sellers and buyers), login, token refresh, logout, password change."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Buyer, RefreshToken, Role, SellerProfile, User, utcnow
from ..schemas import ChangePassword, MeOut, RefreshRequest, RegisterBuyer, RegisterSeller, TokenPair
from ..security import (burn_password_check, get_current_user, hash_password, hash_token, verify_password)
from ..services import auth_service as svc

router = APIRouter(prefix="/auth", tags=["Auth"])


def _create_user(db: Session, request: Request, data, role: Role, profile) -> User:
    email = data.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    user = User(email=email, full_name=data.full_name, phone=data.phone,
                hashed_password=hash_password(data.password), role=role)
    db.add(user)
    try:
        db.flush()  # user.id now exists; unique index catches a simultaneous duplicate
        profile.user_id = user.id
        db.add(profile)
        svc.audit(db, "register", user.id, request, f"role={role.value}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email already registered")
    db.refresh(user)
    return user


@router.post("/register/seller", response_model=MeOut, status_code=201)
def register_seller(data: RegisterSeller, request: Request, db: Session = Depends(get_db)):
    profile = SellerProfile(company_name=data.company_name, gst_number=data.gst_number, city=data.city)
    return _create_user(db, request, data, Role.seller, profile)


@router.post("/register/buyer", response_model=MeOut, status_code=201)
def register_buyer(data: RegisterBuyer, request: Request, db: Session = Depends(get_db)):
    profile = Buyer(org_name=data.org_name, buyer_type=data.buyer_type,
                    annual_demand_kwh=data.annual_demand_kwh,
                    max_price_per_kwh=data.max_price_per_kwh, city=data.city)
    return _create_user(db, request, data, Role.buyer, profile)


@router.post("/login", response_model=TokenPair)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """`username` field = email. Returns a short-lived access token + a refresh token.
    After MAX_FAILED_LOGINS wrong passwords the account is locked for LOCKOUT_MINUTES."""
    now = utcnow()
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if user is None:
        burn_password_check(form.password)
        svc.audit(db, "login_failed", None, request, "unknown email")
        db.commit()
        raise HTTPException(401, "Incorrect email or password")

    if user.locked_until and user.locked_until > now:
        minutes = int((user.locked_until - now).total_seconds() // 60) + 1
        svc.audit(db, "login_blocked_locked", user.id, request)
        db.commit()
        raise HTTPException(429, f"Account temporarily locked. Try again in about {minutes} minute(s).")

    if not verify_password(form.password, user.hashed_password):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
            svc.audit(db, "account_locked", user.id, request)
        else:
            svc.audit(db, "login_failed", user.id, request)
        db.commit()
        raise HTTPException(401, "Incorrect email or password")

    if not user.is_active:
        svc.audit(db, "login_blocked_disabled", user.id, request)
        db.commit()
        raise HTTPException(403, "Account disabled")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    pair = svc.issue_token_pair(db, user, request)
    svc.audit(db, "login", user.id, request)
    db.commit()
    return pair


@router.post("/refresh", response_model=TokenPair)
def refresh(data: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """Swap a refresh token for a fresh pair. The old refresh token stops working."""
    return svc.rotate_refresh_token(db, data.refresh_token, request)


@router.post("/logout", status_code=204)
def logout(data: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """Ends this login session (revokes the refresh token's whole family)."""
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(data.refresh_token)))
    if rt:
        svc.revoke_family(db, rt.family_id)
        svc.audit(db, "logout", rt.user_id, request)
        db.commit()
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Signs the user out everywhere: kills all refresh tokens and all access tokens."""
    svc.revoke_all_for_user(db, user.id)
    user.token_version += 1
    svc.audit(db, "logout_all", user.id, request)
    db.commit()
    return Response(status_code=204)


@router.post("/change-password", status_code=204)
def change_password(data: ChangePassword, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(data.current_password, user.hashed_password):
        svc.audit(db, "password_change_failed", user.id, request)
        db.commit()
        raise HTTPException(400, "Current password is incorrect")
    if data.current_password == data.new_password:
        raise HTTPException(400, "New password must be different")
    user.hashed_password = hash_password(data.new_password)
    user.token_version += 1            # every existing access token becomes invalid
    svc.revoke_all_for_user(db, user.id)  # every existing session must log in again
    svc.audit(db, "password_changed", user.id, request)
    db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return user
