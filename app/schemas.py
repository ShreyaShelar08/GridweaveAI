"""Pydantic schemas = request/response shapes (this is the API contract Yash's frontend uses)."""
from datetime import date, datetime
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, model_validator

from .models import ContractStatus, InvoiceStatus, PaymentStatus, Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth / users ----------
def strong_password(pw: str) -> str:
    """Password policy: 8+ chars with upper, lower and a digit (bcrypt only reads the first 72 bytes)."""
    if len(pw.encode()) > 72:
        raise ValueError("Password must be at most 72 bytes")
    if len(pw) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)):
        raise ValueError("Password needs an uppercase letter, a lowercase letter and a digit")
    return pw


Password = Annotated[str, AfterValidator(strong_password)]
Phone = Annotated[Optional[str], Field(default=None, pattern=r"^\+?[0-9]{10,13}$")]


class RegisterBase(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: Password
    phone: Phone = None


class RegisterSeller(RegisterBase):
    company_name: str = Field(min_length=2, max_length=120)
    gst_number: Optional[str] = Field(default=None, max_length=20)
    city: str = ""


class RegisterBuyer(RegisterBase):
    org_name: str = Field(min_length=2, max_length=120)
    buyer_type: str = Field(pattern="^(factory|mall|hostel|office|other)$")
    annual_demand_kwh: float = Field(gt=0)
    max_price_per_kwh: float = Field(gt=0)
    city: str = ""


class SellerProfileOut(ORM):
    company_name: str
    gst_number: Optional[str] = None
    city: str


class SellerUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    phone: Phone = None
    company_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    gst_number: Optional[str] = Field(default=None, max_length=20)
    city: Optional[str] = None


class BuyerUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    phone: Phone = None
    org_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    buyer_type: Optional[str] = Field(default=None, pattern="^(factory|mall|hostel|office|other)$")
    annual_demand_kwh: Optional[float] = Field(default=None, gt=0)
    max_price_per_kwh: Optional[float] = Field(default=None, gt=0)
    city: Optional[str] = None


class UserOut(ORM):
    id: int
    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    role: Role
    is_active: bool


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access-token lifetime in seconds
    role: Role


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePassword(BaseModel):
    current_password: str
    new_password: Password


class AuditOut(ORM):
    id: int
    user_id: Optional[int]
    action: str
    ip: Optional[str]
    detail: str
    created_at: datetime


# ---------- plants / buyers ----------
class PlantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    capacity_kw: float = Field(gt=0)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    city: str = ""
    state: str = "Maharashtra"
    ask_price_per_kwh: float = Field(gt=0)


class PlantUpdate(BaseModel):
    name: Optional[str] = None
    capacity_kw: Optional[float] = Field(default=None, gt=0)
    ask_price_per_kwh: Optional[float] = Field(default=None, gt=0)
    status: Optional[str] = Field(default=None, pattern="^(listed|unlisted)$")


class PlantOut(ORM):
    id: int
    owner_id: int
    name: str
    capacity_kw: float
    latitude: float
    longitude: float
    city: str
    state: str
    ask_price_per_kwh: float
    status: str


class BuyerCreate(BaseModel):
    org_name: str = Field(min_length=2, max_length=120)
    buyer_type: str = Field(pattern="^(factory|mall|hostel|office|other)$")
    annual_demand_kwh: float = Field(gt=0)
    max_price_per_kwh: float = Field(gt=0)
    city: str = ""


class BuyerOut(ORM):
    id: int
    user_id: int
    org_name: str
    buyer_type: str
    annual_demand_kwh: float
    max_price_per_kwh: float
    city: str


class MeOut(UserOut):
    """Who am I: the user plus their role-specific profile."""
    seller_profile: Optional[SellerProfileOut] = None
    buyer_profile: Optional[BuyerOut] = None


# ---------- contracts ----------
class ContractCreate(BaseModel):
    plant_id: int
    price_per_kwh: float = Field(gt=0)
    monthly_kwh: float = Field(gt=0)
    start_date: date
    end_date: date
    note: str = ""

    @model_validator(mode="after")
    def _dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class CounterOffer(BaseModel):
    price_per_kwh: float = Field(gt=0)
    monthly_kwh: Optional[float] = Field(default=None, gt=0)  # keep current if omitted
    note: str = ""


class OfferOut(ORM):
    id: int
    offered_by_role: str
    price_per_kwh: float
    monthly_kwh: float
    note: str
    created_at: datetime


class ContractOut(ORM):
    id: int
    plant_id: int
    buyer_id: int
    price_per_kwh: float
    monthly_kwh: float
    start_date: date
    end_date: date
    status: ContractStatus
    last_offer_by: str
    created_at: datetime
    activated_at: Optional[datetime] = None


class ContractDetail(ContractOut):
    offers: list[OfferOut] = []


# ---------- readings ----------
class ReadingIn(BaseModel):
    plant_id: int
    timestamp: datetime
    kwh_generated: float = Field(ge=0)
    kwh_delivered: float = Field(ge=0)
    source: str = "simulator"

    @model_validator(mode="after")
    def _delivered_le_generated(self):
        if self.kwh_delivered > self.kwh_generated:
            raise ValueError("kwh_delivered cannot exceed kwh_generated")
        return self


class ReadingsBulk(BaseModel):
    items: list[ReadingIn] = Field(min_length=1, max_length=1000)


class ReadingOut(ORM):
    id: int
    plant_id: int
    contract_id: Optional[int]
    timestamp: datetime
    kwh_generated: float
    kwh_delivered: float
    source: str


class BulkResult(BaseModel):
    inserted: int
    skipped_duplicates: int


# ---------- billing ----------
class InvoiceGenerate(BaseModel):
    contract_id: int
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def _dates(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        return self


class InvoiceOut(ORM):
    id: int
    invoice_number: Optional[str]
    contract_id: int
    period_start: date
    period_end: date
    kwh_billed: float
    price_per_kwh: float
    subtotal: float
    tax_percent: float
    tax_amount: float
    total: float
    status: InvoiceStatus
    issued_at: datetime
    due_date: Optional[date] = None
    paid_at: Optional[datetime] = None
    is_overdue: bool = False


class PaymentOrder(BaseModel):
    """What the frontend needs to open Razorpay Checkout."""
    gateway_order_id: str
    invoice_id: int
    invoice_number: Optional[str] = None
    amount: float            # INR
    amount_paise: int        # what Checkout expects
    currency: str = "INR"
    provider: str            # "razorpay" or "test"
    key_id: Optional[str] = None  # public Razorpay key id (null in test mode)
    mode: str


class PaymentConfirm(BaseModel):
    """Simulator only (PAYMENT_MODE=test)."""
    gateway_order_id: str
    outcome: str = Field(pattern="^(success|failure)$")


class PaymentVerify(BaseModel):
    """The three values Razorpay Checkout hands to the frontend after a successful payment."""
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class PaymentOut(ORM):
    id: int
    invoice_id: int
    invoice_number: Optional[str] = None
    provider: str
    gateway_order_id: str
    provider_payment_id: Optional[str] = None
    method: Optional[str] = None
    amount: float
    currency: str
    status: PaymentStatus
    attempts: int
    failure_reason: Optional[str] = None
    receipt_number: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
