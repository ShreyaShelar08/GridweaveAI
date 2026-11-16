"""Invoice and payment reports (JSON, CSV and PDF), automatically limited to the caller's own data."""
import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PaymentStatus, User
from ..security import get_current_user
from ..services import reports as svc
from ..services.pdf import build_report_pdf

router = APIRouter(prefix="/reports", tags=["Reports"])
Start = Query(None, description="issued on/after (YYYY-MM-DD)")
End = Query(None, description="issued on/before (YYYY-MM-DD)")


def _csv(rows: list[list], filename: str) -> Response:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/summary")
def summary(start: date | None = Start, end: date | None = End,
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Totals: invoices, kWh, billed, paid, outstanding, overdue, collection rate + payment stats."""
    invoices = svc.scoped_invoices(db, user, start, end)
    return {"role": user.role.value, "start": start, "end": end, **svc.summary(invoices),
            "transactions": svc.transaction_stats(svc.scoped_payments(db, user))}


@router.get("/monthly")
def monthly(start: date | None = Start, end: date | None = End,
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Month-wise energy, billed, paid and outstanding."""
    return svc.monthly(svc.scoped_invoices(db, user, start, end))


@router.get("/invoices.csv")
def invoices_csv(start: date | None = Start, end: date | None = End,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = [["invoice_number", "contract_id", "period_start", "period_end", "kwh", "rate_per_kwh", "subtotal",
             "tax", "total", "status", "issued_at", "due_date", "paid_at", "receipt_no", "transaction_id", "method"]]
    for i in svc.scoped_invoices(db, user, start, end):
        p = next((x for x in i.payments if x.status == PaymentStatus.succeeded), None)
        rows.append([i.invoice_number, i.contract_id, i.period_start, i.period_end, i.kwh_billed, i.price_per_kwh,
                     i.subtotal, i.tax_amount, i.total, "overdue" if i.is_overdue else i.status.value,
                     i.issued_at.isoformat(), i.due_date or "", i.paid_at.isoformat() if i.paid_at else "",
                     p.receipt_number if p else "", p.provider_payment_id if p else "", p.method if p else ""])
    return _csv(rows, "invoices.csv")


@router.get("/transactions.csv")
def transactions_csv(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = [["payment_id", "invoice_number", "provider", "gateway_order_id", "transaction_id", "method", "amount_inr",
             "status", "failed_attempts", "failure_reason", "receipt_no", "created_at", "completed_at"]]
    for p in svc.scoped_payments(db, user):
        rows.append([p.id, p.invoice_number, p.provider, p.gateway_order_id, p.provider_payment_id or "",
                     p.method or "", p.amount, p.status.value, p.attempts, p.failure_reason or "",
                     p.receipt_number or "", p.created_at.isoformat(),
                     p.completed_at.isoformat() if p.completed_at else ""])
    return _csv(rows, "transactions.csv")


@router.get("/invoices.pdf")
def invoices_pdf(start: date | None = Start, end: date | None = End,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Printable invoice report with the summary on top."""
    invoices = svc.scoped_invoices(db, user, start, end)
    period = f"{start or 'start'} to {end or 'today'}"
    title = {"seller": "Seller Revenue Report", "buyer": "Buyer Billing Report"}.get(user.role.value, "Platform Invoice Report")
    return Response(build_report_pdf(title, period, svc.summary(invoices), invoices), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="invoice-report.pdf"'})
