"""Invoice / payment reports, always limited to what the logged-in user is allowed to see:
sellers see invoices of their plants, buyers see their own, admins see everything."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Buyer, Contract, Invoice, InvoiceStatus, Payment, PaymentStatus, Plant, Role, User


def scoped_invoices(db: Session, user: User, start: date | None = None, end: date | None = None) -> list[Invoice]:
    q = select(Invoice).join(Contract, Invoice.contract_id == Contract.id)
    if user.role == Role.seller:
        q = q.join(Plant, Contract.plant_id == Plant.id).where(Plant.owner_id == user.id)
    elif user.role == Role.buyer:
        q = q.join(Buyer, Contract.buyer_id == Buyer.id).where(Buyer.user_id == user.id)
    if start:
        q = q.where(Invoice.issued_at >= datetime.combine(start, time.min))
    if end:
        q = q.where(Invoice.issued_at < datetime.combine(end + timedelta(days=1), time.min))
    return list(db.scalars(q.order_by(Invoice.id)).all())


def scoped_payments(db: Session, user: User) -> list[Payment]:
    q = select(Payment).join(Invoice, Payment.invoice_id == Invoice.id).join(Contract, Invoice.contract_id == Contract.id)
    if user.role == Role.seller:
        q = q.join(Plant, Contract.plant_id == Plant.id).where(Plant.owner_id == user.id)
    elif user.role == Role.buyer:
        q = q.join(Buyer, Contract.buyer_id == Buyer.id).where(Buyer.user_id == user.id)
    return list(db.scalars(q.order_by(Payment.id.desc())).all())


def summary(invoices: list[Invoice]) -> dict:
    billed = sum(i.total for i in invoices)
    paid = sum(i.total for i in invoices if i.status == InvoiceStatus.paid)
    overdue = sum(i.total for i in invoices if i.is_overdue)
    return {
        "invoice_count": len(invoices),
        "paid_count": sum(1 for i in invoices if i.status == InvoiceStatus.paid),
        "unpaid_count": sum(1 for i in invoices if i.status != InvoiceStatus.paid),
        "overdue_count": sum(1 for i in invoices if i.is_overdue),
        "total_kwh": round(sum(i.kwh_billed for i in invoices), 3),
        "total_billed": round(billed, 2),
        "amount_paid": round(paid, 2),
        "amount_outstanding": round(billed - paid, 2),
        "amount_overdue": round(overdue, 2),
        "collection_rate_percent": round(paid / billed * 100, 1) if billed else 0.0,
    }


def monthly(invoices: list[Invoice]) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"invoices": 0, "kwh": 0.0, "billed": 0.0, "paid": 0.0})
    for i in invoices:
        b = buckets[f"{i.period_end:%Y-%m}"]
        b["invoices"] += 1
        b["kwh"] += i.kwh_billed
        b["billed"] += i.total
        if i.status == InvoiceStatus.paid:
            b["paid"] += i.total
    return [{"month": m, "invoices": b["invoices"], "kwh": round(b["kwh"], 3), "billed": round(b["billed"], 2),
             "paid": round(b["paid"], 2), "outstanding": round(b["billed"] - b["paid"], 2)}
            for m, b in sorted(buckets.items())]


def transaction_stats(payments: list[Payment]) -> dict:
    return {"total": len(payments),
            "succeeded": sum(1 for p in payments if p.status == PaymentStatus.succeeded),
            "open": sum(1 for p in payments if p.status == PaymentStatus.created),
            "failed_attempts": sum(p.attempts for p in payments)}
