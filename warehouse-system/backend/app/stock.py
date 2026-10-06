"""原料库存只提供记账及只读查询，不开放余额编辑/删除。"""
from fastapi import APIRouter, Query
from typing import Literal
from sqlalchemy import and_, select, func, or_, case
from sqlalchemy.orm import aliased

from app.auth import SessionDep, UserDep
from app.models import (RawMaterial, MaterialBatch, Supplier, Location, InventoryTransaction,
                        StockDocument, User, MaterialMovement, MaterialIssueRecord,
                        ProductionOrderMaterial, ProductionOrder, MaterialTransfer, MaterialCount)
from app.schemas import (Envelope, MaterialReceiptInput, MaterialOutboundInput,
                         MaterialPutawayInput, MaterialTransferInput, MaterialCountInput, MaterialAdjustmentInput)
from app.stock_service import post_material, business_today, fefo_conditions, fefo_order
from app.material_location_service import putaway, transfer, count_stock, adjust_stock

router = APIRouter(prefix='/api', tags=['原材料库存'])


@router.post('/material-receipts', response_model=Envelope)
def receipt(payload: MaterialReceiptInput, user: UserDep, session: SessionDep):
    return Envelope(data=post_material(session, user, payload, 'material_receipt'))


@router.post('/material-outbounds', response_model=Envelope)
def outbound(payload: MaterialOutboundInput, user: UserDep, session: SessionDep):
    return Envelope(data=post_material(session, user, payload, 'material_outbound'))


@router.post('/material-putaways', response_model=Envelope)
def putaway_stock(payload: MaterialPutawayInput, user: UserDep, session: SessionDep):
    return Envelope(data=putaway(session, user, payload))


@router.post('/material-transfers', response_model=Envelope)
def transfer_stock(payload: MaterialTransferInput, user: UserDep, session: SessionDep):
    return Envelope(data=transfer(session, user, payload))


@router.post('/material-counts', response_model=Envelope)
def count_material_stock(payload: MaterialCountInput, user: UserDep, session: SessionDep):
    return Envelope(data=count_stock(session, user, payload))


@router.post('/material-adjustments', response_model=Envelope)
def adjust_material_stock(payload: MaterialAdjustmentInput, user: UserDep, session: SessionDep):
    return Envelope(data=adjust_stock(session, user, payload))


def page_result(session, query, page, page_size):
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = session.execute(query.offset((page - 1) * page_size).limit(page_size)).mappings()
    from decimal import Decimal
    from datetime import date, datetime
    items = [{key: format(value, 'f') if isinstance(value, Decimal) else value.isoformat()
              if isinstance(value, (date, datetime)) else value for key, value in row.items()} for row in rows]
    return Envelope(data={'items': items, 'total': total, 'page': page, 'page_size': page_size})


def receipt_query(q='', status=None):
    reversal = aliased(StockDocument)
    reversed_id = select(reversal.id).where(reversal.reverses_document_id == StockDocument.id).correlate(StockDocument).scalar_subquery()
    pending = case((reversed_id.is_not(None), 0), else_=MaterialMovement.quantity - MaterialMovement.shelved_quantity)
    query = select(StockDocument.id, StockDocument.document_no, StockDocument.created_at,
                   MaterialMovement.id.label('receipt_movement_id'), MaterialMovement.batch_id,
                   RawMaterial.id.label('material_id'), RawMaterial.code.label('material_code'),
                   RawMaterial.name.label('material_name'), MaterialBatch.batch_no, MaterialBatch.unit,
                   MaterialBatch.production_date, MaterialBatch.expiry_date,
                   Supplier.name.label('supplier_name'), MaterialBatch.supplier_id,
                   MaterialMovement.quantity.label('received_quantity'),
                   MaterialMovement.shelved_quantity, pending.label('pending_quantity'),
                   reversed_id.label('reversed_by_document_id'),
                   case((reversed_id.is_not(None), 'reversed'),
                        (MaterialMovement.shelved_quantity == 0, 'pending'),
                        (MaterialMovement.shelved_quantity == MaterialMovement.quantity, 'shelved'),
                        else_='partial').label('status'))
    query = query.select_from(MaterialMovement).join(StockDocument, StockDocument.id == MaterialMovement.document_id)
    query = query.join(MaterialBatch, MaterialBatch.id == MaterialMovement.batch_id).join(RawMaterial).join(Supplier)
    query = query.where(StockDocument.kind == 'material_receipt')
    if q.strip():
        query = query.where(or_(RawMaterial.name.contains(q.strip(), autoescape=True),
                                RawMaterial.code.contains(q.strip(), autoescape=True),
                                MaterialBatch.batch_no.contains(q.strip(), autoescape=True),
                                StockDocument.document_no.contains(q.strip(), autoescape=True)))
    if status == 'open':
        query = query.where(pending > 0)
    elif status == 'pending':
        query = query.where(MaterialMovement.shelved_quantity == 0, reversed_id.is_(None))
    elif status == 'partial':
        query = query.where(MaterialMovement.shelved_quantity > 0, pending > 0)
    elif status == 'shelved':
        query = query.where(pending == 0, reversed_id.is_(None))
    elif status == 'reversed':
        query = query.where(reversed_id.is_not(None))
    return query


@router.get('/material-receipts', response_model=Envelope)
def receipts(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
             status: str | None = Query(None, pattern='^(open|pending|partial|shelved|reversed)$'),
             page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return page_result(session, receipt_query(q, status).order_by(StockDocument.id.desc()), page, page_size)


@router.get('/material-receipts/{identity}', response_model=Envelope)
def receipt_detail(identity: int, user: UserDep, session: SessionDep):
    from fastapi import HTTPException
    receipt = session.execute(receipt_query().where(StockDocument.id == identity)).mappings().first()
    if receipt is None:
        raise HTTPException(404, '原材料入库单不存在')
    reversal = aliased(StockDocument)
    allocations = session.execute(select(MaterialTransfer.id, MaterialTransfer.document_id, MaterialTransfer.quantity,
                                         reversal.id.label('reversed_by_document_id'),
                                         StockDocument.created_at, Location.name.label('location_name'))
                                  .join(StockDocument, StockDocument.id == MaterialTransfer.document_id)
                                  .outerjoin(reversal, reversal.reverses_document_id == StockDocument.id)
                                  .join(MaterialBatch, MaterialBatch.id == MaterialTransfer.target_batch_id)
                                  .join(Location, Location.id == MaterialBatch.location_id)
                                  .where(MaterialTransfer.receipt_movement_id == receipt['receipt_movement_id'])
                                  .order_by(MaterialTransfer.id)).mappings().all()
    from decimal import Decimal
    from datetime import date, datetime
    summary = {key: format(value, 'f') if isinstance(value, Decimal) else value.isoformat()
               if isinstance(value, (date, datetime)) else value for key, value in receipt.items()}
    return Envelope(data={**summary, 'allocations': [
        {'id': row['id'], 'document_id': row['document_id'],
         'reversed_by_document_id': row['reversed_by_document_id'], 'quantity': format(row['quantity'], 'f'),
         'created_at': row['created_at'].isoformat(), 'location_name': row['location_name']}
        for row in allocations]})


@router.get('/material-location-stock', response_model=Envelope)
def location_stock(user: UserDep, session: SessionDep, location_id: int | None = Query(None, gt=0),
                   material_id: int | None = Query(None, gt=0), batch_no: str = Query('', max_length=80),
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = batch_query('', material_id).where(MaterialBatch.remaining_quantity > 0)
    if location_id:
        query = query.where(MaterialBatch.location_id == location_id)
    if batch_no.strip():
        query = query.where(MaterialBatch.batch_no.contains(batch_no.strip(), autoescape=True))
    return page_result(session, query.order_by(Location.name, RawMaterial.name, MaterialBatch.batch_no, MaterialBatch.id), page, page_size)


@router.get('/material-location-stock/{identity}', response_model=Envelope)
def location_stock_detail(identity: int, user: UserDep, session: SessionDep):
    from fastapi import HTTPException
    row = session.execute(batch_query('', None).where(MaterialBatch.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '库位库存记录不存在')
    from decimal import Decimal
    from datetime import date, datetime
    detail = {key: format(value, 'f') if isinstance(value, Decimal) else value.isoformat()
              if isinstance(value, (date, datetime)) else value for key, value in row.items()}
    detail['transactions'] = transactions(user, session, q='', material_id=None, batch_id=identity,
                                          kind=None, location_id=None, page=1, page_size=100).data
    return Envelope(data=detail)


@router.get('/material-counts', response_model=Envelope)
def counts(user: UserDep, session: SessionDep, batch_id: int | None = Query(None, gt=0),
           page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = select(MaterialCount.id, MaterialCount.document_id, MaterialCount.batch_id,
                   MaterialCount.book_quantity, MaterialCount.actual_quantity, MaterialCount.difference,
                   MaterialCount.counted_at, MaterialCount.reason, User.display_name.label('counted_by_name'),
                   RawMaterial.name.label('material_name'), MaterialBatch.batch_no,
                   Location.name.label('location_name'), MaterialBatch.unit)
    query = query.join(MaterialBatch, MaterialBatch.id == MaterialCount.batch_id).join(RawMaterial)
    query = query.join(Location, Location.id == MaterialBatch.location_id).join(User, User.id == MaterialCount.counted_by)
    if batch_id:
        query = query.where(MaterialCount.batch_id == batch_id)
    return page_result(session, query.order_by(MaterialCount.id.desc()), page, page_size)


@router.get('/material-transfers', response_model=Envelope)
def transfers(user: UserDep, session: SessionDep, kind: str | None = Query(None, pattern='^(putaway|transfer)$'),
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    reversal = aliased(StockDocument)
    source = aliased(MaterialBatch)
    target = aliased(MaterialBatch)
    source_location = aliased(Location)
    target_location = aliased(Location)
    query = select(MaterialTransfer.id, MaterialTransfer.document_id, MaterialTransfer.quantity,
                   StockDocument.kind, StockDocument.document_no, StockDocument.created_at,
                   reversal.id.label('reversed_by_document_id'),
                   RawMaterial.name.label('material_name'), source.batch_no, source.unit,
                   source_location.name.label('source_location_name'),
                   target_location.name.label('target_location_name'), User.display_name.label('operator_name'))
    query = query.join(StockDocument, StockDocument.id == MaterialTransfer.document_id)
    query = query.outerjoin(reversal, reversal.reverses_document_id == StockDocument.id)
    query = query.join(source, source.id == MaterialTransfer.source_batch_id)
    query = query.join(target, target.id == MaterialTransfer.target_batch_id)
    query = query.join(RawMaterial, RawMaterial.id == source.material_id)
    query = query.outerjoin(source_location, source_location.id == source.location_id)
    query = query.join(target_location, target_location.id == target.location_id)
    query = query.join(User, User.id == StockDocument.operator_id)
    if kind:
        query = query.where(StockDocument.kind == kind)
    return page_result(session, query.order_by(MaterialTransfer.id.desc()), page, page_size)


def batch_query(q, material_id):
    # 批次列表包含全部余额用于追溯；只有专用 FEFO 接口才进一步排除过期、零库存和失效档案。
    query = select(MaterialBatch.id, MaterialBatch.material_id, RawMaterial.code.label('material_code'),
                   RawMaterial.name.label('material_name'), MaterialBatch.batch_no,
                   MaterialBatch.received_quantity, MaterialBatch.remaining_quantity, MaterialBatch.unit,
                   MaterialBatch.production_date, MaterialBatch.expiry_date, MaterialBatch.received_at,
                   MaterialBatch.supplier_id, Supplier.name.label('supplier_name'), MaterialBatch.location_id,
                   Location.name.label('location_name'), (MaterialBatch.expiry_date < business_today()).label('expired'))
    query = query.join(RawMaterial, RawMaterial.id == MaterialBatch.material_id).join(Supplier).join(Location).where(
        MaterialBatch.stock_status == 'shelved', MaterialBatch.location_id.is_not(None))
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
         purpose: Literal['normal', 'supplier_return', 'scrap'] = Query('normal'),
         page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = batch_query('', material_id).where(*fefo_conditions(
        material_id, include_expired=purpose != 'normal')).order_by(*fefo_order())
    # 此结果是页面选批建议，实际出库/领料仍会在写事务中锁行并再次校验。
    return page_result(session, query, page, page_size)


@router.get('/material-stock', response_model=Envelope)
def stock(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    # 汇总库存包含该原料所有批次（包括已过期批次）；预警和可用库存使用各自更严格的口径。
    query = select(RawMaterial.id, RawMaterial.code, RawMaterial.name, RawMaterial.unit,
                   func.coalesce(func.sum(MaterialBatch.remaining_quantity), 0).label('remaining_quantity'))
    query = query.outerjoin(MaterialBatch, and_(MaterialBatch.material_id == RawMaterial.id,
                            MaterialBatch.stock_status == 'shelved', MaterialBatch.location_id.is_not(None))).where(RawMaterial.deleted_at.is_(None))
    if q.strip():
        query = query.where(or_(RawMaterial.code.contains(q.strip(), autoescape=True), RawMaterial.name.contains(q.strip(), autoescape=True)))
    query = query.group_by(RawMaterial.id, RawMaterial.code, RawMaterial.name, RawMaterial.unit).order_by(RawMaterial.id)
    return page_result(session, query, page, page_size)


@router.get('/material-transactions', response_model=Envelope)
def transactions(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
                 material_id: int | None = Query(None, gt=0), batch_id: int | None = Query(None, gt=0),
                 kind: str | None = Query(None, pattern='^(material_receipt|material_outbound|issue|supplement|return|putaway|transfer|count_gain|count_loss|adjustment|reversal)$'),
                 location_id: int | None = Query(None, gt=0),
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    # 统一流水同时关联普通出入库明细和生产领补退明细，便于从库存变化追溯到原始输入和生产单。
    reversed_document = aliased(StockDocument)
    source_location = aliased(Location)
    target_location = aliased(Location)
    query = select(InventoryTransaction.id, InventoryTransaction.created_at, InventoryTransaction.kind,
                   MaterialBatch.material_id, RawMaterial.code.label('material_code'), RawMaterial.name.label('material_name'),
                   MaterialBatch.id.label('batch_id'), MaterialBatch.batch_no, InventoryTransaction.unit,
                   InventoryTransaction.before_quantity, InventoryTransaction.change_quantity, InventoryTransaction.after_quantity,
                   StockDocument.id.label('document_id'), StockDocument.document_no,
                   StockDocument.material_outbound_purpose, Supplier.name.label('supplier_name'),
                   User.display_name.label('operator_name'),
                   InventoryTransaction.reverses_transaction_id, StockDocument.reverses_document_id,
                   reversed_document.id.label('reversed_by_document_id'),
                   InventoryTransaction.source_location_id, source_location.name.label('source_location_name'),
                   InventoryTransaction.target_location_id, target_location.name.label('target_location_name'),
                   InventoryTransaction.notes,
                   func.coalesce(MaterialMovement.input_quantity, MaterialIssueRecord.input_quantity).label('input_quantity'),
                   func.coalesce(MaterialMovement.input_unit, MaterialIssueRecord.input_unit).label('input_unit'),
                   ProductionOrder.id.label('production_order_id'), ProductionOrder.order_no.label('production_order_no'))
    query = query.select_from(InventoryTransaction).join(MaterialBatch, MaterialBatch.id == InventoryTransaction.material_batch_id)
    query = query.join(RawMaterial).join(StockDocument, StockDocument.id == InventoryTransaction.document_id)
    query = query.join(Supplier, Supplier.id == MaterialBatch.supplier_id)
    query = query.outerjoin(reversed_document, reversed_document.reverses_document_id == StockDocument.id)
    query = query.join(User, User.id == InventoryTransaction.operator_id)
    query = query.outerjoin(source_location, source_location.id == InventoryTransaction.source_location_id)
    query = query.outerjoin(target_location, target_location.id == InventoryTransaction.target_location_id)
    query = query.outerjoin(MaterialMovement, and_(MaterialMovement.document_id == StockDocument.id,
                                                    MaterialMovement.batch_id == MaterialBatch.id))
    query = query.outerjoin(MaterialIssueRecord, and_(MaterialIssueRecord.document_id == StockDocument.id,
                                                       MaterialIssueRecord.batch_id == MaterialBatch.id))
    query = query.outerjoin(ProductionOrderMaterial,
                            ProductionOrderMaterial.id == MaterialIssueRecord.order_material_id)
    query = query.outerjoin(ProductionOrder,
                            ProductionOrder.id == ProductionOrderMaterial.production_order_id)
    if q.strip():
        query = query.where(or_(*(column.contains(q.strip(), autoescape=True) for column in
                                (RawMaterial.code, RawMaterial.name, MaterialBatch.batch_no,
                                 StockDocument.document_no, ProductionOrder.order_no))))
    if material_id:
        query = query.where(MaterialBatch.material_id == material_id)
    if batch_id:
        query = query.where(MaterialBatch.id == batch_id)
    if kind:
        query = query.where(InventoryTransaction.kind == kind)
    if location_id:
        query = query.where(or_(InventoryTransaction.source_location_id == location_id,
                                InventoryTransaction.target_location_id == location_id))
    return page_result(session, query.order_by(InventoryTransaction.id.desc()), page, page_size)
