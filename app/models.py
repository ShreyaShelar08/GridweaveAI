"""Database schema: users (+ seller/buyer profiles), refresh tokens, audit log, plants, contracts
(+ negotiation offers), readings, invoices, payments."""
import enum
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC (SQLite-friendly)


class Role(str, enum.Enum):
    admin = "admin"
    seller = "seller"
    buyer = "buyer"


class ContractStatus(str, enum.Enum):
    proposed = "proposed"
    negotiating = "negotiating"
    active = "active"
    rejected = "rejected"
    terminated = "terminated"


class InvoiceStatus(str, enum.Enum):
    issued = "issued"
    paid = "paid"


class PaymentStatus(str, enum.Enum):
    created = "created"
    succeeded = "succeeded"
    failed = "failed"


def _enum(e):
    return Enum(e, native_enum=False, length=20)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role))
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    # --- login security ---
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # bump = all access tokens die
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    plants: Mapped[list["Plant"]] = relationship(back_populates="owner")
    buyer_profile: Mapped[Optional["Buyer"]] = relationship(back_populates="user", uselist=False)
    seller_profile: Mapped[Optional["SellerProfile"]] = relationship(back_populates="user", uselist=False)


class SellerProfile(Base):
    """Extra details every seller gives at registration (buyers have the `buyers` table)."""
    __tablename__ = "seller_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    company_name: Mapped[str] = mapped_column(String(120))
    gst_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    city: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="seller_profile")


class RefreshToken(Base):
    """Long-lived login token. Only a SHA-256 hash is stored, never the token itself.
    Tokens are rotated on every use; `family_id` groups a login session so a stolen/reused
    token can revoke the whole session."""
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class AuditLog(Base):
    """Security trail: register, login, failed login, lockout, logout, password change..."""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Plant(Base):
    __tablename__ = "plants"
    __table_args__ = (
        CheckConstraint("capacity_kw > 0", name="ck_plant_capacity_pos"),
        CheckConstraint("ask_price_per_kwh > 0", name="ck_plant_price_pos"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    capacity_kw: Mapped[float] = mapped_column(Float)
    latitude: Mapped[float] = mapped_column(Float)   # used by Shreya's weather ingestion
    longitude: Mapped[float] = mapped_column(Float)
    city: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(80), default="Maharashtra")
    ask_price_per_kwh: Mapped[float] = mapped_column(Float)  # INR
    status: Mapped[str] = mapped_column(String(20), default="listed")  # listed / unlisted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    owner: Mapped[User] = relationship(back_populates="plants")
    contracts: Mapped[list["Contract"]] = relationship(back_populates="plant")


class Buyer(Base):
    __tablename__ = "buyers"
    __table_args__ = (
        CheckConstraint("annual_demand_kwh > 0", name="ck_buyer_demand_pos"),
        CheckConstraint("max_price_per_kwh > 0", name="ck_buyer_price_pos"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    org_name: Mapped[str] = mapped_column(String(120))
    buyer_type: Mapped[str] = mapped_column(String(30))  # factory / mall / hostel / office / other
    annual_demand_kwh: Mapped[float] = mapped_column(Float)
    max_price_per_kwh: Mapped[float] = mapped_column(Float)  # INR
    city: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="buyer_profile")
    contracts: Mapped[list["Contract"]] = relationship(back_populates="buyer")


class Contract(Base):
    """A VPPA between one plant and one buyer."""
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("price_per_kwh > 0", name="ck_contract_price_pos"),
        CheckConstraint("monthly_kwh > 0", name="ck_contract_volume_pos"),
        CheckConstraint("end_date > start_date", name="ck_contract_dates"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id"), index=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"), index=True)
    price_per_kwh: Mapped[float] = mapped_column(Float)   # current agreed/offered price
    monthly_kwh: Mapped[float] = mapped_column(Float)     # committed monthly volume
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[ContractStatus] = mapped_column(_enum(ContractStatus), default=ContractStatus.proposed)
    last_offer_by: Mapped[str] = mapped_column(String(10))  # "buyer" / "seller"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    terminated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    plant: Mapped[Plant] = relationship(back_populates="contracts")
    buyer: Mapped[Buyer] = relationship(back_populates="contracts")
    offers: Mapped[list["ContractOffer"]] = relationship(
        back_populates="contract", order_by="ContractOffer.id", cascade="all, delete-orphan"
    )


class ContractOffer(Base):
    """Negotiation history: every offer / counter-offer is stored."""
    __tablename__ = "contract_offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"), index=True)
    offered_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    offered_by_role: Mapped[str] = mapped_column(String(10))
    price_per_kwh: Mapped[float] = mapped_column(Float)
    monthly_kwh: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    contract: Mapped[Contract] = relationship(back_populates="offers")


class Reading(Base):
    """Metered energy per plant per time slot (hourly in the prototype).
    kwh_delivered = energy that counts towards the contract (billed)."""
    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("plant_id", "timestamp", name="uq_reading_plant_ts"),
        Index("ix_reading_contract_ts", "contract_id", "timestamp"),
        CheckConstraint("kwh_generated >= 0 AND kwh_delivered >= 0", name="ck_reading_non_negative"),
        CheckConstraint("kwh_delivered <= kwh_generated", name="ck_reading_delivered_le_generated"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id"), index=True)
    contract_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    kwh_generated: Mapped[float] = mapped_column(Float)
    kwh_delivered: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(30), default="simulator")  # simulator / inverter / meter


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (CheckConstraint("total >= 0 AND kwh_billed >= 0", name="ck_invoice_non_negative"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    kwh_billed: Mapped[float] = mapped_column(Float)
    price_per_kwh: Mapped[float] = mapped_column(Float)
    subtotal: Mapped[float] = mapped_column(Float)
    tax_percent: Mapped[float] = mapped_column(Float, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float)
    status: Mapped[InvoiceStatus] = mapped_column(_enum(InvoiceStatus), default=InvoiceStatus.issued)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    contract: Mapped[Contract] = relationship()
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice", order_by="Payment.id")

    @property
    def is_overdue(self) -> bool:
        return self.status == InvoiceStatus.issued and self.due_date is not None and self.due_date < date.today()


class Payment(Base):
    """One payment attempt/order for an invoice (a real transaction record).
    provider = "razorpay" (real gateway) or "test" (simulator)."""
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_payment_amount_pos"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20), default="test", server_default="test")
    gateway_order_id: Mapped[str] = mapped_column(String(60), unique=True)       # Razorpay order_xxx
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, unique=True)  # pay_xxx
    method: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)      # upi / card / netbanking ...
    amount: Mapped[float] = mapped_column(Float)                                   # INR
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    status: Mapped[PaymentStatus] = mapped_column(_enum(PaymentStatus), default=PaymentStatus.created)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # failed tries on this order
    failure_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    receipt_number: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="payments")

    @property
    def invoice_number(self) -> Optional[str]:
        return self.invoice.invoice_number


class WebhookEvent(Base):
    """Every webhook the gateway sends us. The unique event id makes processing idempotent
    (Razorpay may deliver the same event more than once)."""
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(80), unique=True)
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
