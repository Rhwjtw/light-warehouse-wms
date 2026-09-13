"""原料库存只提供记账及只读查询，不开放余额编辑/删除。"""
from fastapi import APIRouter, Query
from sqlalchemy import select, func, or_

from app.auth import SessionDep, UserDep
from app.models import (RawMaterial, MaterialBatch, Supplier, Location, InventoryTransaction,
                        StockDocument, User, MaterialMovement)
from app.schemas import Envelope, MaterialReceiptInput, MaterialOutboundInput
from app.stock_service import post_material, business_today

router = APIRouter(prefix='/api', tags=['原材料库存'])


@router.post('/material-receipts', response_model=Envelope)
def receipt(payload: MaterialReceiptInput, user: UserDep, session: SessionDep):
    return Envelope(data=post_material(session, user, payload, 'material_receipt'))


@router.post('/material-outbounds', response_model=Envelope)
def outbound(payload: MaterialOutboundInput, user: UserDep, session: SessionDep):
    return Envelope(data=post_material(session, user, payload, 'material_outbound'))


def page_result(session, query, page, page_size):
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = session.execute(query.offset((page - 1) * page_size).limit(page_size)).mappings()
    from decimal import Decimal
    from datetime import date, datetime
    items = [{key: format(value, 'f') if isinstance(value, Decimal) else value.isoformat()
              if isinstance(value, (date, datetime)) else value for key, value in row.items()} for row in rows]
    return Envelope(data={'items': items, 'total': total, 'page': page, 'page_size': page_size})


def batch_query(q, material_id):
    query = select(MaterialBatch.id, MaterialBatch.material_id, RawMaterial.code.label('material_code'),
                   RawMaterial.name.label('material_name'), MaterialBatch.batch_no,
                   MaterialBatch.received_quantity, MaterialBatch.remaining_quantity, MaterialBatch.unit,
                   MaterialBatch.production_date, MaterialBatch.expiry_date, MaterialBatch.received_at,
                   MaterialBatch.supplier_id, Supplier.name.label('supplier_name'), MaterialBatch.location_id,
                   Location.name.label('location_name'), (MaterialBatch.expiry_date < business_today()).label('expired'))
    query = query.join(RawMaterial, RawMaterial.id == MaterialBatch.material_id).join(Supplier).join(Location)
    if q.strip():
        query = query.where(or_(*(column.contains(q.strip(), autoescape=True) for column in
                                (RawMaterial.code, RawMaterial.name, MaterialBatch.batch_no))))
    if material_id:
        query = query.where(MaterialBatch.material_id == material_id)
    return query


@router.get('/material-batches', response_model=Envelope)
def batches(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
            material_id: int | None = Query(None, gt=0), page: int = Query(1, ge=1),
            page_size: int = Query(20, ge=1, le=100)):
    return page_result(session, batch_query(q, material_id).order_by(MaterialBatch.expiry_date, MaterialBatch.received_at, MaterialBatch.id), page, page_size)


@router.get('/material-fefo', response_model=Envelope)
def fefo(user: UserDep, session: SessionDep, material_id: int = Query(..., gt=0),
         page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = batch_query('', material_id).where(
        MaterialBatch.remaining_quantity > 0, MaterialBatch.expiry_date >= business_today(),
        MaterialBatch.unit == RawMaterial.unit, RawMaterial.is_active.is_(True), RawMaterial.deleted_at.is_(None),
        Supplier.is_active.is_(True), Supplier.deleted_at.is_(None),
        Location.is_active.is_(True), Location.deleted_at.is_(None)
    ).order_by(MaterialBatch.expiry_date, MaterialBatch.received_at, MaterialBatch.id)
    return page_result(session, query, page, page_size)


@router.get('/material-stock', response_model=Envelope)
def stock(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(RawMaterial.id, RawMaterial.code, RawMaterial.name, RawMaterial.unit,
                   func.coalesce(func.sum(MaterialBatch.remaining_quantity), 0).label('remaining_quantity'))
    query = query.outerjoin(MaterialBatch, MaterialBatch.material_id == RawMaterial.id).where(RawMaterial.deleted_at.is_(None))
    if q.strip():
        query = query.where(or_(RawMaterial.code.contains(q.strip(), autoescape=True), RawMaterial.name.contains(q.strip(), autoescape=True)))
    query = query.group_by(RawMaterial.id, RawMaterial.code, RawMaterial.name, RawMaterial.unit).order_by(RawMaterial.id)
    return page_result(session, query, page, page_size)


@router.get('/material-transactions', response_model=Envelope)
def transactions(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
                 material_id: int | None = Query(None, gt=0), batch_id: int | None = Query(None, gt=0),
                 kind: str | None = Query(None, pattern='^(material_receipt|material_outbound)$'),
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(InventoryTransaction.id, InventoryTransaction.created_at, InventoryTransaction.kind,
                   MaterialBatch.material_id, RawMaterial.code.label('material_code'), RawMaterial.name.label('material_name'),
                   MaterialBatch.id.label('batch_id'), MaterialBatch.batch_no, InventoryTransaction.unit,
                   InventoryTransaction.before_quantity, InventoryTransaction.change_quantity, InventoryTransaction.after_quantity,
                   StockDocument.id.label('document_id'), StockDocument.document_no, User.display_name.label('operator_name'),
                   InventoryTransaction.notes, MaterialMovement.input_quantity, MaterialMovement.input_unit)
    query = query.select_from(InventoryTransaction).join(MaterialBatch, MaterialBatch.id == InventoryTransaction.material_batch_id)
    query = query.join(RawMaterial).join(StockDocument, StockDocument.id == InventoryTransaction.document_id)
    query = query.join(User, User.id == InventoryTransaction.operator_id).join(MaterialMovement, MaterialMovement.document_id == StockDocument.id)
    if q.strip():
        query = query.where(or_(*(column.contains(q.strip(), autoescape=True) for column in
                                (RawMaterial.code, RawMaterial.name, MaterialBatch.batch_no, StockDocument.document_no))))
    if material_id:
        query = query.where(MaterialBatch.material_id == material_id)
    if batch_id:
        query = query.where(MaterialBatch.id == batch_id)
    if kind:
        query = query.where(InventoryTransaction.kind == kind)
    return page_result(session, query.order_by(InventoryTransaction.id.desc()), page, page_size)
