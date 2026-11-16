"""Admin-only: user management and platform stats."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditLog, Contract, ContractStatus, Invoice, Plant, Role, User
from ..schemas import AuditOut, UserOut
from ..security import require_roles

router = APIRouter(prefix="/admin", tags=["Admin"])
admin_only = require_roles(Role.admin)


@router.get("/users", response_model=list[UserOut])
def users(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.id)).all()


@router.patch("/users/{user_id}/active", response_model=UserOut)
def set_active(user_id: int, active: bool, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target.id == admin.id:
        raise HTTPException(400, "You cannot disable yourself")
    target.is_active = active
    db.commit()
    return target


@router.post("/users/{user_id}/unlock", response_model=UserOut)
def unlock(user_id: int, _: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Clears a brute-force lockout."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    target.locked_until, target.failed_login_count = None, 0
    db.commit()
    return target


@router.get("/audit-log", response_model=list[AuditOut])
def audit_log(user_id: int | None = None, action: str | None = None, limit: int = 100,
              _: User = Depends(admin_only), db: Session = Depends(get_db)):
    q = select(AuditLog)
    if user_id is not None:
        q = q.where(AuditLog.user_id == user_id)
    if action:
        q = q.where(AuditLog.action == action)
    return db.scalars(q.order_by(AuditLog.id.desc()).limit(min(limit, 500))).all()


@router.get("/stats")
def stats(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    return {
        "users": db.scalar(select(func.count(User.id))),
        "plants": db.scalar(select(func.count(Plant.id))),
        "active_contracts": db.scalar(select(func.count(Contract.id)).where(
            Contract.status == ContractStatus.active)),
        "invoiced_total_inr": db.scalar(select(func.coalesce(func.sum(Invoice.total), 0.0))),
    }
