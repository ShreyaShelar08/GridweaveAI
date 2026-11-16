"""Shared payment bookkeeping used by the verify endpoint, the simulator and the webhook,
so an invoice is marked paid in exactly one way (and only once)."""
from sqlalchemy.orm import Session

from ..models import InvoiceStatus, Payment, PaymentStatus, utcnow
from . import auth_service as audit_svc


def mark_paid(db: Session, payment: Payment, provider_payment_id: str, method: str | None, request=None) -> Payment:
    """Idempotent: calling it twice for the same payment changes nothing the second time."""
    if payment.status == PaymentStatus.succeeded:
        return payment
    invoice = payment.invoice
    buyer_user_id = invoice.contract.buyer.user_id
    now = utcnow()
    payment.status = PaymentStatus.succeeded
    payment.provider_payment_id = provider_payment_id
    payment.method = method
    payment.completed_at = now
    payment.receipt_number = f"RCPT-{now:%Y%m}-{payment.id:05d}"
    if invoice.status == InvoiceStatus.paid:
        # money arrived twice for one invoice -> keep the record, flag it for a manual refund
        payment.failure_reason = "DUPLICATE PAYMENT - invoice was already paid, refund required"
        audit_svc.audit(db, "duplicate_payment", buyer_user_id, request, f"payment={payment.id}")
    else:
        invoice.status = InvoiceStatus.paid
        invoice.paid_at = now
        audit_svc.audit(db, "payment_succeeded", buyer_user_id, request,
                        f"invoice={invoice.invoice_number} amount={payment.amount} method={method}")
    return payment


def mark_attempt_failed(db: Session, payment: Payment, reason: str, request=None) -> Payment:
    """A failed attempt (card declined, UPI timeout...). The order stays open so the buyer can retry."""
    if payment.status == PaymentStatus.succeeded:
        return payment
    payment.attempts += 1
    payment.failure_reason = (reason or "payment failed")[:255]
    audit_svc.audit(db, "payment_attempt_failed", payment.invoice.contract.buyer.user_id, request,
                    f"payment={payment.id} reason={payment.failure_reason}")
    return payment
