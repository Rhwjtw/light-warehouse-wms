"""生产单与车间领料、补料、退料 API。"""
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.auth import SessionDep, UserDep
from app.models import ProductionOrder
from app.production_service import (
    cancel_order, create_order, fefo_suggestion, find_order, order_data,
    post_material, update_order,
)
from app.schemas import (
    Envelope, ProductionMaterialPostingInput, ProductionOrderCreateInput,
    ProductionOrderUpdateInput,
    ProductionCompleteInput,
)
from app.settlement_service import preview as settlement_preview, complete as complete_order

router = APIRouter(prefix='/api/production-orders', tags=['生产单'])

@router.post('/{identity}/settlement-preview', response_model=Envelope)
def preview(identity: int, payload: ProductionCompleteInput, user: UserDep, session: SessionDep):
    return Envelope(data=settlement_preview(session, user, identity, payload))


@router.post('/{identity}/complete', response_model=Envelope)
def complete(identity: int, payload: ProductionCompleteInput, user: UserDep, session: SessionDep):
    return Envelope(data=complete_order(session, user, identity, payload))


@router.get('', response_model=Envelope)
def list_orders(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
                status: str | None = Query(None, pattern='^(draft|ready|in_progress|completed|received|cancelled)$'),
                page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    conditions = []
    if q.strip():
        conditions.append(or_(
            ProductionOrder.order_no.contains(q.strip(), autoescape=True),
            ProductionOrder.product_name_snapshot.contains(q.strip(), autoescape=True),
        ))
    if status:
        conditions.append(ProductionOrder.status == status)
    base = select(ProductionOrder.id).where(*conditions)
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    orders = session.scalars(
        select(ProductionOrder).where(*conditions).order_by(ProductionOrder.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Envelope(data={
        'items': [order_data(session, order, include_details=False) for order in orders],
        'total': total, 'page': page, 'page_size': page_size,
    })


@router.post('', response_model=Envelope)
def create(payload: ProductionOrderCreateInput, user: UserDep, session: SessionDep):
    return Envelope(data=create_order(session, user, payload))


@router.get('/{identity}', response_model=Envelope)
def detail(identity: int, user: UserDep, session: SessionDep):
    return Envelope(data=order_data(session, find_order(session, identity)))


@router.put('/{identity}', response_model=Envelope)
def update(identity: int, payload: ProductionOrderUpdateInput, user: UserDep, session: SessionDep):
    return Envelope(data=update_order(session, user, identity, payload))


@router.post('/{identity}/cancel', response_model=Envelope)
def cancel(identity: int, user: UserDep, session: SessionDep):
    return Envelope(data=cancel_order(session, user, identity))


@router.post('/{identity}/issue', response_model=Envelope)
def issue(identity: int, payload: ProductionMaterialPostingInput, user: UserDep, session: SessionDep):
    return Envelope(data=post_material(session, user, identity, payload, 'issue'))


@router.post('/{identity}/supplement', response_model=Envelope)
def supplement(identity: int, payload: ProductionMaterialPostingInput, user: UserDep, session: SessionDep):
    return Envelope(data=post_material(session, user, identity, payload, 'supplement'))


@router.post('/{identity}/return', response_model=Envelope)
def return_material(identity: int, payload: ProductionMaterialPostingInput, user: UserDep, session: SessionDep):
    return Envelope(data=post_material(session, user, identity, payload, 'return'))


@router.get('/{identity}/materials/{material_id}/fefo-suggestion', response_model=Envelope)
def suggestion(identity: int, material_id: int, user: UserDep, session: SessionDep,
               quantity: Decimal | None = Query(None, gt=0, max_digits=20, decimal_places=6)):
    return Envelope(data=fefo_suggestion(session, identity, material_id, quantity))
