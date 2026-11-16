"""Password hashing, JWT access tokens, opaque refresh tokens and role-based access control (RBAC)."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import Role, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---------------- passwords ----------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


_dummy_hash: str | None = None


def burn_password_check(password: str) -> None:
    """Run a bcrypt check against a fake hash so 'unknown email' takes as long as 'wrong password'
    (stops attackers discovering which emails are registered by timing the response)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("not-a-real-password")
    verify_password(password, _dummy_hash)


# ---------------- tokens ----------------
def create_access_token(user: User) -> tuple[str, int]:
    """Short-lived JWT. `tv` (token version) lets us kill all of a user's tokens instantly."""
    now = datetime.now(timezone.utc)
    lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "tv": user.token_version,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm), int(lifetime.total_seconds())


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Refresh tokens are random, so a fast SHA-256 is enough (and lets us look them up by hash)."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------- current user + RBAC ----------------
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        if payload.get("type") != "access":
            raise cred_error
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise cred_error
    user = db.get(User, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise cred_error
    return user


def require_roles(*roles: Role):
    """Dependency factory: `Depends(require_roles(Role.seller, Role.admin))`."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role is not allowed to do this")
        return user

    return checker
