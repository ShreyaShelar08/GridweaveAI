"""VPPA contract lifecycle:
   buyer proposes -> parties counter-offer alternately -> the party who did NOT make the
   last offer accepts -> ACTIVE.   Either party may reject; either may terminate an active one."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Buyer, Contract, ContractOffer, ContractStatus, Plant, Role, User, utcnow
from ..schemas import ContractCreate, ContractDetail, ContractOut, CounterOffer
from ..security import get_current_user, require_roles

router = APIRouter(prefix="/contracts", tags=["Marketplace & Contracts"])
OPEN = (ContractStatus.proposed, ContractStatus.negotiating)


def party_of(contract: Contract, user: User):
    if contract.plant.owner_id == user.id:
        return "seller"
    if contract.buyer.user_id == user.id:
        return "buyer"
    return None


def load_contract(db: Session, contract_id: int, user: User) -> tuple[Contract, str | None]:
    contract = db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found")
    party = party_of(contract, user)
    if party is None and user.role != Role.admin:
        raise HTTPException(403, "You are not a party to this contract")
    return contract, party


def need_party(party):
    if party is None:
        raise HTTPException(403, "Only the buyer or seller of this contract can do this")


def add_offer(db, contract, user, party, price, kwh, note):
    db.add(ContractOffer(contract_id=contract.id, offered_by_user_id=user.id, offered_by_role=party,
                         price_per_kwh=price, monthly_kwh=kwh, note=note))
    contract.price_per_kwh, contract.monthly_kwh, contract.last_offer_by = price, kwh, party


@router.post("", response_model=ContractOut, status_code=201)
def propose_contract(data: ContractCreate, user: User = Depends(require_roles(Role.buyer)),
                     db: Session = Depends(get_db)):
    buyer = user.buyer_profile
    if not buyer:
        raise HTTPException(400, "Create your buyer profile first")
    plant = db.get(Plant, data.plant_id)
    if not plant or plant.status != "listed":
        raise HTTPException(404, "Plant not available")
    contract = Contract(plant_id=plant.id, buyer_id=buyer.id, price_per_kwh=data.price_per_kwh,
                        monthly_kwh=data.monthly_kwh, start_date=data.start_date,
                        end_date=data.end_date, last_offer_by="buyer")
    db.add(contract)
    db.flush()
    contract.plant, contract.buyer = plant, buyer
    add_offer(db, contract, user, "buyer", data.price_per_kwh, data.monthly_kwh, data.note)
    db.commit()
    db.refresh(contract)
    return contract


@router.get("", response_model=list[ContractOut])
def my_contracts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(Contract)
    if user.role == Role.seller:
        q = q.join(Plant).where(Plant.owner_id == user.id)
    elif user.role == Role.buyer:
        q = q.join(Buyer).where(Buyer.user_id == user.id)
    return db.scalars(q.order_by(Contract.id.desc())).all()


@router.get("/{contract_id}", response_model=ContractDetail)
def contract_detail(contract_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return load_contract(db, contract_id, user)[0]


@router.post("/{contract_id}/negotiate", response_model=ContractDetail)
def negotiate(contract_id: int, data: CounterOffer, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    contract, party = load_contract(db, contract_id, user)
    need_party(party)
    if contract.status not in OPEN:
        raise HTTPException(409, f"Contract is {contract.status.value}; cannot negotiate")
    if contract.last_offer_by == party:
        raise HTTPException(409, "Waiting for the other party to respond to your last offer")
    add_offer(db, contract, user, party, data.price_per_kwh,
              data.monthly_kwh or contract.monthly_kwh, data.note)
    contract.status = ContractStatus.negotiating
    db.commit()
    db.refresh(contract)
    return contract


@router.post("/{contract_id}/accept", response_model=ContractDetail)
def accept(contract_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Accepting the other party's latest offer ACTIVATES the VPPA."""
    contract, party = load_contract(db, contract_id, user)
    need_party(party)
    if contract.status not in OPEN:
        raise HTTPException(409, f"Contract is {contract.status.value}")
    if contract.last_offer_by == party:
        raise HTTPException(409, "You cannot accept your own offer")
    clash = db.scalar(select(Contract.id).where(
        Contract.plant_id == contract.plant_id, Contract.status == ContractStatus.active,
        Contract.start_date <= contract.end_date, Contract.end_date >= contract.start_date))
    if clash:
        raise HTTPException(409, f"Plant already has active contract #{clash} in these dates")
    contract.status = ContractStatus.active
    contract.activated_at = utcnow()
    db.commit()
    db.refresh(contract)
    return contract


@router.post("/{contract_id}/reject", response_model=ContractOut)
def reject(contract_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contract, party = load_contract(db, contract_id, user)
    need_party(party)
    if contract.status not in OPEN:
        raise HTTPException(409, f"Contract is {contract.status.value}")
    contract.status = ContractStatus.rejected
    db.commit()
    return contract


@router.post("/{contract_id}/terminate", response_model=ContractOut)
def terminate(contract_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contract, party = load_contract(db, contract_id, user)
    need_party(party)
    if contract.status != ContractStatus.active:
        raise HTTPException(409, "Only active contracts can be terminated")
    contract.status = ContractStatus.terminated
    contract.terminated_at = utcnow()
    db.commit()
    return contract
