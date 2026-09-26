"""Append-only reversal of ordinary outbound documents in the request transaction."""
import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import audit, require_permission
from app.models import (FinishedBatch, FinishedOutbound, FinishedProduct, InventoryTransaction,
                        Location, MaterialBatch, MaterialMovement, RawMaterial, StockDocument,
                        Supplier, User)
from app.stock_service import MAX_QUANTITY, business_today

SUPPORTED_KINDS = {'material_outbound', 'finished_outbound'}


def _reversal_for(session, original_id):
    return session.scalar(select(StockDocument).where(StockDocument.reverses_document_id == original_id))


def _document(session, identity):
    document = session.get(StockDocument, identity)
    if document is None:
        raise HTTPException(404, '库存单据不存在')
    return document


def document_detail(session, identity):
    document = _document(session, identity)
    reversed_by = _reversal_for(session, document.id) if document.kind != 'reversal' else None
    original = session.get(StockDocument, document.reverses_document_id) if document.reverses_document_id else None
    operator = session.get(User, document.operator_id)
    transactions = list(session.scalars(select(InventoryTransaction).where(
        InventoryTransaction.document_id == document.id).order_by(InventoryTransaction.id)))
    rows = []
    for row in transactions:
        if row.material_batch_id is not None:
            batch = session.get(MaterialBatch, row.material_batch_id)
            material = session.get(RawMaterial, batch.material_id) if batch else None
            name = material.name if material else None
            batch_no = batch.batch_no if batch else None
        else:
            batch = session.get(FinishedBatch, row.finished_batch_id)
            product = session.get(FinishedProduct, batch.product_id) if batch else None
            name = product.name if product else None
            batch_no = None  # 成品第一版只有汇总库存，不把内部行误称为真实批次。
        source = session.get(Location, row.source_location_id) if row.source_location_id else None
        target = session.get(Location, row.target_location_id) if row.target_location_id else None
        rows.append({'id': row.id, 'reverses_transaction_id': row.reverses_transaction_id,
                     'material_batch_id': row.material_batch_id, 'finished_batch_id': row.finished_batch_id,
                     'item_name': name, 'batch_no': batch_no, 'unit': row.unit,
                     'before_quantity': format(row.before_quantity, 'f'),
                     'change_quantity': format(row.change_quantity, 'f'),
                     'after_quantity': format(row.after_quantity, 'f'),
                     'source_location_name': source.name if source else None,
                     'target_location_name': target.name if target else None,
                     'created_at': row.created_at.isoformat()})
    return {'id': document.id, 'document_no': document.document_no, 'kind': document.kind,
            'business_type': document.business_type, 'business_date': document.business_date.isoformat()
            if document.business_date else None, 'reference_no': document.reference_no,
            'operator_id': document.operator_id, 'operator_name': operator.display_name if operator else None,
            'created_at': document.created_at.isoformat(), 'notes': document.notes,
            'status': 'reversal' if document.kind == 'reversal' else 'reversed' if reversed_by else 'normal',
            'reverses_document_id': document.reverses_document_id,
            'reverses_kind': document.reverses_kind,
            'original_document_no': document.original_document_no_snapshot,
            'reversal_reason': document.reversal_reason,
            'original': {'id': original.id, 'document_no': original.document_no} if original else None,
            'reversal': {'id': reversed_by.id, 'document_no': reversed_by.document_no,
                         'reason': reversed_by.reversal_reason,
                         'operator_id': reversed_by.operator_id,
                         'created_at': reversed_by.created_at.isoformat()} if reversed_by else None,
            'transactions': rows}


def _plan(session, identity, *, lock=False):
    document = _document(session, identity)
    if document.kind not in SUPPORTED_KINDS:
        raise HTTPException(409, '本批只支持原材料普通出库和成品普通出库冲销')
    original_rows = list(session.scalars(select(InventoryTransaction).where(
        InventoryTransaction.document_id == identity)))
    if len(original_rows) != 1 or original_rows[0].kind != document.kind or original_rows[0].change_quantity >= 0:
        raise HTTPException(409, '原单库存流水不完整，不能冲销；请先核查')
    original_row = original_rows[0]
    if document.kind == 'material_outbound':
        batch_id = original_row.material_batch_id
        material_id = session.scalar(select(MaterialBatch.material_id).where(MaterialBatch.id == batch_id))
        if material_id is None:
            raise HTTPException(409, '原单批次不存在，不能冲销')
        query = select(RawMaterial).where(RawMaterial.id == material_id)
        material = session.scalar(query.with_for_update() if lock else query)
    else:
        batch_id = original_row.finished_batch_id
        product_id = session.scalar(select(FinishedBatch.product_id).where(FinishedBatch.id == batch_id))
        if product_id is None:
            raise HTTPException(409, '原单成品库存不存在，不能冲销')
        query = select(FinishedProduct).where(FinishedProduct.id == product_id)
        product = session.scalar(query.with_for_update() if lock else query)
    if lock:
        document = session.scalar(select(StockDocument).where(StockDocument.id == identity).with_for_update())
    if _reversal_for(session, identity):
        raise HTTPException(409, '原单已冲销，不能重复冲销')
    if document.kind == 'material_outbound':
        query = select(MaterialBatch).where(MaterialBatch.id == batch_id)
        batch = session.scalar(query.with_for_update() if lock else query)
        movement = session.scalar(select(MaterialMovement).where(MaterialMovement.document_id == identity))
        supplier = session.get(Supplier, batch.supplier_id) if batch else None
        location = session.get(Location, batch.location_id) if batch and batch.location_id else None
        if (movement is None or movement.batch_id != batch_id or movement.quantity != -original_row.change_quantity
                or original_row.finished_batch_id is not None or batch is None or batch.stock_status != 'shelved'
                or batch.location_id is None or original_row.source_location_id != batch.location_id
                or material is None or not material.is_active or material.deleted_at or batch.unit != material.unit
                or supplier is None or not supplier.is_active or supplier.deleted_at
                or location is None or not location.is_active or location.deleted_at):
            raise HTTPException(409, '原单批次、库位或关联档案已变化，不能直接冲销')
        if batch.remaining_quantity - original_row.change_quantity > batch.received_quantity:
            raise HTTPException(409, '原批次当前余额无法恢复本次出库数量')
        item_name, batch_no, location_name = material.name, batch.batch_no, location.name
    else:
        query = select(FinishedBatch).where(FinishedBatch.id == batch_id)
        batch = session.scalar(query.with_for_update() if lock else query)
        outbound = session.scalar(select(FinishedOutbound).where(FinishedOutbound.document_id == identity))
        if (document.business_type != 'normal' or outbound is None or outbound.batch_id != batch_id
                or outbound.quantity != -original_row.change_quantity or original_row.material_batch_id is not None
                or batch is None or batch.stock_scope != 'aggregate' or product is None or not product.is_active
                or product.deleted_at or batch.unit != product.unit):
            raise HTTPException(409, '原单成品或汇总库存已变化，不能直接冲销')
        item_name, batch_no, location_name = product.name, None, None
    before = batch.remaining_quantity
    change = -original_row.change_quantity
    after = before + change
    if after > MAX_QUANTITY:
        raise HTTPException(409, '冲销后库存超过数量上限')
    return document, original_row, batch, {
        'item_name': item_name, 'batch_no': batch_no, 'location_name': location_name,
        'batch_id': batch.id, 'unit': batch.unit,
        'before_quantity': format(before, 'f'), 'change_quantity': format(change, 'f'),
        'after_quantity': format(after, 'f'), 'reverses_transaction_id': original_row.id,
    }


def reversal_preview(session, identity):
    document = _document(session, identity)
    try:
        _, _, _, change = _plan(session, identity)
        return {'original_document_id': identity, 'original_document_no': document.document_no,
                'original_kind': document.kind, 'eligible': True, 'message': '可以冲销；提交时将重新检查实时库存',
                'changes': [change]}
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return {'original_document_id': identity, 'original_document_no': document.document_no,
                'original_kind': document.kind, 'eligible': False, 'message': str(exc.detail), 'changes': []}


def reverse_document(session, user, identity, payload):
    require_permission(user, session, 'stock.reversal.write')
    fingerprint = hashlib.sha256(json.dumps({'original_document_id': identity, 'reason': payload.reason,
                                             'notes': payload.notes}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def replay(existing):
        if (existing.kind != 'reversal' or existing.reverses_document_id != identity
                or existing.operator_id != user.id or existing.request_hash != fingerprint):
            raise HTTPException(409, '请求标识已被其他业务使用')
        return document_detail(session, existing.id)

    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing:
        return replay(existing)
    document, original_row, batch, change = _plan(session, identity, lock=True)
    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing:
        return replay(existing)
    reversal = StockDocument(document_no='RV-' + uuid4().hex, request_id=payload.request_id,
                             request_hash=fingerprint, business_date=business_today(),
                             business_type=document.business_type or 'normal',
                             reference_no=document.document_no, kind='reversal',
                             reverses_document_id=document.id, reverses_kind=document.kind,
                             original_document_no_snapshot=document.document_no,
                             reversal_reason=payload.reason, operator_id=user.id, notes=payload.notes)
    try:
        with session.begin_nested():
            session.add(reversal)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
        if existing:
            return replay(existing)
        raise HTTPException(409, '原单已被其他请求冲销') from None
    before = batch.remaining_quantity
    amount = -original_row.change_quantity
    batch.remaining_quantity = before + amount
    session.flush()
    session.add(InventoryTransaction(document_id=reversal.id, reverses_transaction_id=original_row.id,
                                     material_batch_id=original_row.material_batch_id,
                                     finished_batch_id=original_row.finished_batch_id, kind='reversal',
                                     unit=original_row.unit, before_quantity=before, change_quantity=amount,
                                     after_quantity=batch.remaining_quantity, operator_id=user.id,
                                     source_location_id=None, target_location_id=original_row.source_location_id,
                                     notes=payload.reason))
    session.flush()
    audit(session, user.id, 'reverse_stock_document', 'stock_documents', reversal.id,
          {'original_document_id': document.id, 'original_document_no': document.document_no,
           'original_kind': document.kind, 'reason': payload.reason,
           'change_quantity': change['change_quantity']})
    session.flush()
    return document_detail(session, reversal.id)
