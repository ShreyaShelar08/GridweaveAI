"""Invoices, PDFs and payments. Works with the built-in simulator (PAYMENT_MODE=test)
or the real Razorpay gateway (PAYMENT_MODE=razorpay)."""
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Contract, Invoice, InvoiceStatus, Payment, PaymentStatus, Role, User, utcnow
from ..schemas import (InvoiceGenerate, InvoiceOut, PaymentConfirm, PaymentOrder, PaymentOut, PaymentVerify)
from ..security import get_current_user, require_roles
from ..services import auth_service as audit_svc
from ..services import billing as billing_service
from ..services import payment_service
from ..services import payments as gateways
from ..services import reports
from ..services.pdf import build_invoice_pdf, build_receipt_pdf

router = APIRouter(prefix="/billing", tags=["Billing & Payments"])


def _party(db: Session, invoice: Invoice, user: User) -> str | None:
    c = invoice.contract
    return "seller" if c.plant.owner_id == user.id else "buyer" if c.buyer.user_id == user.id else None


def _load_invoice(db: Session, invoice_id: int, user: User) -> tuple[Invoice, str | None]:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    party = _party(db, inv, user)
    if party is None and user.role != Role.admin:
        raise HTTPException(403, "Not your invoice")
    return inv, party


def _load_payment(db: Session, payment_id: int, user: User) -> Payment:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(404, "Payment not found")
    _load_invoice(db, payment.invoice_id, user)  # same access rule as the invoice
    return payment


def _buyer_payment_by_order(db: Session, order_id: str, user: User) -> Payment:
    payment = db.scalar(select(Payment).where(Payment.gateway_order_id == order_id))
    if not payment:
        raise HTTPException(404, "Order not found")
    if _party(db, payment.invoice, user) != "buyer":
        raise HTTPException(403, "Not your payment")
    return payment


# ------------------------- invoices -------------------------
@router.post("/generate", response_model=InvoiceOut, status_code=201)
def generate(data: InvoiceGenerate, user: User = Depends(require_roles(Role.seller, Role.admin)),
             db: Session = Depends(get_db)):
    """Metered kWh x contract price. Seller of the contract (or admin) triggers it."""
    contract = db.get(Contract, data.contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found")
    if user.role != Role.admin and contract.plant.owner_id != user.id:
        raise HTTPException(403, "Not your contract")
    return billing_service.generate_invoice(db, contract, data.period_start, data.period_end)


@router.get("/invoices", response_model=list[InvoiceOut])
def my_invoices(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(reversed(reports.scoped_invoices(db, user)))


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def invoice_detail(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _load_invoice(db, invoice_id, user)[0]


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv, _ = _load_invoice(db, invoice_id, user)
    return Response(build_invoice_pdf(inv), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv.invoice_number}.pdf"'})


# ------------------------- payments -------------------------
@router.post("/invoices/{invoice_id}/pay", response_model=PaymentOrder)
def start_payment(invoice_id: int, request: Request, user: User = Depends(require_roles(Role.buyer)),
                  db: Session = Depends(get_db)):
    """Step 1: buyer starts paying. Creates (or reuses) a gateway order and returns what the
    frontend needs to open Razorpay Checkout."""
    inv, party = _load_invoice(db, invoice_id, user)
    if party != "buyer":
        raise HTTPException(403, "Only the buyer of this contract can pay")
    if inv.status == InvoiceStatus.paid:
        raise HTTPException(409, "Invoice already paid")
    gw = gateways.get_gateway()

    # reuse a recent unfinished order so one invoice never has two live orders (avoids double charges)
    payment = db.scalar(select(Payment).where(
        Payment.invoice_id == inv.id, Payment.status == PaymentStatus.created, Payment.provider == gw.name,
        Payment.created_at > utcnow() - timedelta(minutes=30)).order_by(Payment.id.desc()))
    if payment is None:
        order = gw.create_order(inv.invoice_number, inv.total)
        payment = Payment(invoice_id=inv.id, provider=gw.name, gateway_order_id=order.order_id,
                          amount=inv.total, currency=order.currency)
        db.add(payment)
        audit_svc.audit(db, "payment_order_created", user.id, request, f"invoice={inv.invoice_number}")
        db.commit()
    return PaymentOrder(gateway_order_id=payment.gateway_order_id, invoice_id=inv.id,
                        invoice_number=inv.invoice_number, amount=payment.amount,
                        amount_paise=gateways.to_paise(payment.amount), currency=payment.currency,
                        provider=gw.name, key_id=gw.public_key, mode=settings.payment_mode)


@router.post("/payments/verify", response_model=PaymentOut)
def verify_payment(data: PaymentVerify, request: Request, user: User = Depends(require_roles(Role.buyer)),
                   db: Session = Depends(get_db)):
    """Step 2 (Razorpay): after Checkout succeeds the frontend sends the 3 values here.
    We verify the signature, ask Razorpay for the real payment status, check the amount, then mark paid."""
    gw = gateways.get_gateway()
    if gw.name != "razorpay":
        raise HTTPException(400, "Razorpay is not enabled (PAYMENT_MODE is not 'razorpay')")
    payment = _buyer_payment_by_order(db, data.razorpay_order_id, user)
    if payment.status == PaymentStatus.succeeded:
        return payment  # idempotent: the webhook may already have done it

    if not gw.verify_signature(data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature):
        audit_svc.audit(db, "payment_signature_invalid", user.id, request, f"order={data.razorpay_order_id}")
        db.commit()
        raise HTTPException(400, "Payment signature is invalid")

    details = gw.fetch_payment(data.razorpay_payment_id)
    if details.get("order_id") != payment.gateway_order_id or details.get("amount") != gateways.to_paise(payment.amount):
        audit_svc.audit(db, "payment_mismatch", user.id, request, f"payment={payment.id}")
        db.commit()
        raise HTTPException(400, "Payment does not match this order")
    if details.get("status") == "authorized":  # accounts without auto-capture
        details = gw.capture(data.razorpay_payment_id, details["amount"])
    if details.get("status") != "captured":
        raise HTTPException(409, f"Payment is {details.get('status')}, not completed yet")

    payment_service.mark_paid(db, payment, data.razorpay_payment_id, details.get("method"), request)
    db.commit()
    return payment


@router.post("/payments/confirm", response_model=PaymentOut)
def confirm_payment(data: PaymentConfirm, request: Request, user: User = Depends(require_roles(Role.buyer)),
                    db: Session = Depends(get_db)):
    """Step 2 (SIMULATOR only, PAYMENT_MODE=test): pretend the gateway reported success or failure."""
    if gateways.get_gateway().name != "test":
        raise HTTPException(400, "Simulator is disabled; use /billing/payments/verify")
    payment = _buyer_payment_by_order(db, data.gateway_order_id, user)
    if payment.status != PaymentStatus.created:
        raise HTTPException(409, f"Payment already {payment.status.value}")
    if data.outcome == "success":
        payment_service.mark_paid(db, payment, f"pay_test_{uuid.uuid4().hex[:14]}", "simulated", request)
    else:
        payment.status = PaymentStatus.failed
        payment.failure_reason = "simulated failure"
        payment.completed_at = utcnow()
    db.commit()
    return payment


@router.get("/payments", response_model=list[PaymentOut])
def my_payments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Transaction history (buyer: what I paid, seller: what I received, admin: everything)."""
    return reports.scoped_payments(db, user)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def payment_detail(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _load_payment(db, payment_id, user)


@router.get("/payments/{payment_id}/receipt")
def payment_receipt(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment = _load_payment(db, payment_id, user)
    if payment.status != PaymentStatus.succeeded:
        raise HTTPException(409, "Receipt is available only for successful payments")
    return Response(build_receipt_pdf(payment), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{payment.receipt_number}.pdf"'})


