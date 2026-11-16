"""Login-session logic: issue / rotate / revoke refresh tokens and write the audit trail."""
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuditLog, RefreshToken, User, utcnow
from ..security import create_access_token, hash_token, new_refresh_token


def client_ip(request: Request | None) -> str | None:
    return request.client.host if request is not None and request.client else None


def audit(db: Session, action: str, user_id: int | None = None, request: Request | None = None,
          detail: str = "") -> None:
    """Adds an audit row. The caller commits (so it is saved together with the action itself)."""
    db.add(AuditLog(user_id=user_id, action=action, ip=client_ip(request), detail=detail[:500]))


def issue_token_pair(db: Session, user: User, request: Request | None, family_id: str | None = None) -> dict:
    access, expires_in = create_access_token(user)
    raw_refresh = new_refresh_token()
    ua = request.headers.get("user-agent", "")[:255] if request is not None else None
    db.add(RefreshToken(
        user_id=user.id,
        family_id=family_id or uuid.uuid4().hex,
        token_hash=hash_token(raw_refresh),
        expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
        ip=client_ip(request),
        user_agent=ua,
    ))
    return {"access_token": access, "refresh_token": raw_refresh, "token_type": "bearer",
            "expires_in": expires_in, "role": user.role}


def revoke_family(db: Session, family_id: str) -> None:
    db.execute(update(RefreshToken)
               .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


def revoke_all_for_user(db: Session, user_id: int) -> None:
    db.execute(update(RefreshToken)
               .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


def rotate_refresh_token(db: Session, raw_token: str, request: Request) -> dict:
    """Exchange a refresh token for a new access+refresh pair. The old refresh token dies.
    If an already-used token shows up again it was probably stolen, so the whole session is revoked."""
    invalid = HTTPException(401, "Invalid or expired refresh token")
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if rt is None:
        raise invalid
    if rt.revoked_at is not None:
        revoke_family(db, rt.family_id)
        audit(db, "refresh_reuse_detected", rt.user_id, request, "session revoked")
        db.commit()
        raise invalid
    if rt.expires_at < utcnow():
        raise invalid
    user = db.get(User, rt.user_id)
    if user is None or not user.is_active:
        raise invalid
    rt.revoked_at = utcnow()
    pair = issue_token_pair(db, user, request, family_id=rt.family_id)
    audit(db, "token_refreshed", user.id, request)
    db.commit()
    return pair
