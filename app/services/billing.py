"""Billing engine:  invoice = metered kWh delivered  x  contract price (+ tax)."""
from datetime import datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Contract, ContractStatus, Invoice, Reading, utcnow


def generate_invoice(db: Session, contract: Contract, period_start, period_end) -> Invoice:
    if contract.status not in (ContractStatus.active, ContractStatus.terminated):
        raise HTTPException(409, "Only active/terminated contracts can be billed")

    # 1. prevent double billing: no overlap with an existing invoice of this contract
    overlap = db.scalar(
        select(Invoice.id).where(
            Invoice.contract_id == contract.id,
            Invoice.period_start <= period_end,
            Invoice.period_end >= period_start,
        )
    )
    if overlap:
        raise HTTPException(409, f"Period overlaps existing invoice #{overlap}")

    # 2. sum metered energy for this contract in [period_start 00:00, period_end+1 00:00)
    start_dt = datetime.combine(period_start, time.min)
    end_dt = datetime.combine(period_end + timedelta(days=1), time.min)
    kwh = db.scalar(
        select(func.coalesce(func.sum(Reading.kwh_delivered), 0.0)).where(
            Reading.contract_id == contract.id,
            Reading.timestamp >= start_dt,
            Reading.timestamp < end_dt,
        )
    )
    if kwh <= 0:
        raise HTTPException(422, "No delivered energy found for this period")

    # 3. money maths (rounded to paise)
    rate = contract.price_per_kwh
    subtotal = round(kwh * rate, 2)
    tax_amount = round(subtotal * settings.tax_percent / 100, 2)
    invoice = Invoice(
        contract_id=contract.id,
        period_start=period_start,
        period_end=period_end,
        kwh_billed=round(kwh, 3),
        price_per_kwh=rate,
        subtotal=subtotal,
        tax_percent=settings.tax_percent,
        tax_amount=tax_amount,
        total=round(subtotal + tax_amount, 2),
        due_date=(utcnow() + timedelta(days=settings.payment_terms_days)).date(),
    )
    db.add(invoice)
    db.flush()  # get the id
    invoice.invoice_number = f"GW-{period_end:%Y%m}-{invoice.id:05d}"
    db.commit()
    db.refresh(invoice)
    return invoice
