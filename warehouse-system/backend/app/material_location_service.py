"""Raw material shelving, location transfers and stock counts in the request transaction."""
import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import audit, require_permission
from app.models import (InventoryTransaction, Location, MaterialAdjustment, MaterialBatch, MaterialCount,
                        MaterialMovement, MaterialTransfer, RawMaterial, StockDocument,
                        Supplier, utcnow)
from app.stock_service import active_record, business_today
from app.unit_conversion import MAX_QUANTITY


def _fingerprint(payload):
    content = payload.model_dump(mode='json', exclude={'request_id'})
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _result(session, document):
    data = {'id': document.id, 'document_no': document.document_no, 'request_id': document.request_id,
            'kind': document.kind}
    if document.kind in ('putaway', 'transfer'):
        transfer = session.scalar(select(MaterialTransfer).where(MaterialTransfer.document_id == document.id))
        data.update(source_batch_id=transfer.source_batch_id, target_batch_id=transfer.target_batch_id,
                    quantity=format(transfer.quantity, 'f'))
    elif document.kind == 'adjustment':
        adjustment = session.scalar(select(MaterialAdjustment).where(MaterialAdjustment.document_id == document.id))
        data.update(batch_id=adjustment.batch_id, before_quantity=format(adjustment.before_quantity, 'f'),
                    change_quantity=format(adjustment.change_quantity, 'f'),
                    after_quantity=format(adjustment.after_quantity, 'f'))
    else:
        count = session.scalar(select(MaterialCount).where(MaterialCount.document_id == document.id))
        data.update(batch_id=count.batch_id, book_quantity=format(count.book_quantity, 'f'),
                    actual_quantity=format(count.actual_quantity, 'f'), difference=format(count.difference, 'f'))
    return data


def _existing(session, payload, fingerprint, allowed_kinds, user):
    document = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if document is None:
        return None
    if document.kind not in allowed_kinds or document.operator_id != user.id or document.request_hash != fingerprint:
        raise HTTPException(409, '请求标识已使用且内容或操作者不同，请刷新后重试')
    return _result(session, document)


def _document(session, payload, fingerprint, kind, user, prefix, reference_no=None):
    document = StockDocument(document_no=prefix + uuid4().hex, request_id=payload.request_id,
                             request_hash=fingerprint, business_date=business_today(), business_type='normal',
                             reference_no=reference_no, kind=kind, operator_id=user.id,
                             notes=getattr(payload, 'notes', None) or getattr(payload, 'reason', None))
    try:
        with session.begin_nested():
            session.add(document)
            session.flush()
    except IntegrityError:
        return None
    return document


def _material_for_batch(session, batch_id):
    material_id = session.scalar(select(MaterialBatch.material_id).where(MaterialBatch.id == batch_id))
    if material_id is None:
        raise HTTPException(404, '批次不存在')
    return active_record(session, RawMaterial, material_id)


def _target_batch(session, source, location_id, quantity):
    batch = session.scalar(select(MaterialBatch).where(
        MaterialBatch.material_id == source.material_id, MaterialBatch.batch_no == source.batch_no,
        MaterialBatch.supplier_id == source.supplier_id, MaterialBatch.location_id == location_id,
        MaterialBatch.stock_status == 'shelved').with_for_update())
    if batch:
        if (batch.unit, batch.production_date, batch.expiry_date) != (source.unit, source.production_date, source.expiry_date):
            raise HTTPException(409, '目标库位同批号的单位或生产/到期日期不一致')
        if batch.received_quantity + quantity > MAX_QUANTITY or batch.remaining_quantity + quantity > MAX_QUANTITY:
            raise HTTPException(422, '目标库位批次数量超出范围')
        before = batch.remaining_quantity
        batch.received_quantity += quantity
        batch.remaining_quantity += quantity
    else:
        before = Decimal('0')
        batch = MaterialBatch(material_id=source.material_id, batch_no=source.batch_no,
                              received_quantity=quantity, remaining_quantity=quantity, unit=source.unit,
                              production_date=source.production_date, expiry_date=source.expiry_date,
                              supplier_id=source.supplier_id, location_id=location_id,
                              stock_status='shelved', received_at=source.received_at, notes=source.notes)
        session.add(batch)
    session.flush()
    return batch, before


def _ledger(session, document, batch, user, before, change, source_location_id, target_location_id):
    session.add(InventoryTransaction(document_id=document.id, material_batch_id=batch.id, kind=document.kind,
                                     unit=batch.unit, before_quantity=before, change_quantity=change,
                                     after_quantity=before + change, operator_id=user.id, notes=document.notes,
                                     source_location_id=source_location_id, target_location_id=target_location_id))
    session.flush()


def _move(session, user, payload, kind):
    require_permission(user, session, 'material.inventory.write')
    fingerprint = _fingerprint(payload)
    replay = _existing(session, payload, fingerprint, (kind,), user)
    if replay:
        return replay
    if kind == 'putaway':
        movement = session.get(MaterialMovement, payload.receipt_movement_id)
        if movement is None:
            raise HTTPException(404, '原材料入库记录不存在')
        source_id = movement.batch_id
        target_location_id = payload.location_id
    else:
        source_id = payload.source_batch_id
        target_location_id = payload.target_location_id
    material = _material_for_batch(session, source_id)
    # All raw stock writers lock the material first, then existing batch IDs in ascending order.
    replay = _existing(session, payload, fingerprint, (kind,), user)
    if replay:
        return replay
    movement = None
    if kind == 'putaway':
        movement = session.scalar(select(MaterialMovement).where(
            MaterialMovement.id == payload.receipt_movement_id).execution_options(populate_existing=True))
        receipt = session.get(StockDocument, movement.document_id) if movement else None
        if receipt is None or receipt.kind != 'material_receipt' or movement.shelved_quantity is None:
            raise HTTPException(422, '所选记录不是待上架入库')
        if session.scalar(select(StockDocument.id).where(StockDocument.reverses_document_id == receipt.id)):
            raise HTTPException(409, '原入库单已冲销，不能继续上架')
        source_id = movement.batch_id
    # Material mutex is already held. Lock existing source/target balances in PK order.
    source_info = session.scalar(select(MaterialBatch).where(MaterialBatch.id == source_id)
                                 .execution_options(populate_existing=True))
    if source_info is None:
        raise HTTPException(409, '原批次不存在')
    target_id = session.scalar(select(MaterialBatch.id).where(
        MaterialBatch.material_id == source_info.material_id, MaterialBatch.batch_no == source_info.batch_no,
        MaterialBatch.supplier_id == source_info.supplier_id, MaterialBatch.location_id == target_location_id,
        MaterialBatch.stock_status == 'shelved'))
    lock_ids = sorted({source_id, target_id} - {None})
    locked = {}
    for batch_id in lock_ids:
        batch = session.scalar(select(MaterialBatch).where(MaterialBatch.id == batch_id)
                               .with_for_update().execution_options(populate_existing=True))
        if batch is not None:
            locked[batch.id] = batch
    source = locked.get(source_id)
    if source is None or source.material_id != material.id:
        raise HTTPException(409, '原材料批次已变化，请刷新后重试')
    if source.unit != material.unit:
        raise HTTPException(409, '批次单位与原材料库存单位不一致')
    if kind == 'putaway':
        if source.stock_status != 'pending' or source.location_id is not None:
            raise HTTPException(422, '所选批次不是待上架库存')
        if payload.quantity > movement.quantity - movement.shelved_quantity:
            raise HTTPException(409, '上架数量超过该入库单待上架数量')
    elif source.stock_status != 'shelved' or source.location_id is None:
        raise HTTPException(422, '只能移出已上架的库位库存')
    if payload.quantity > source.remaining_quantity:
        raise HTTPException(409, '原库位库存不足')
    if source.location_id == target_location_id:
        raise HTTPException(422, '原库位和目标库位不能相同')
    active_record(session, Supplier, source.supplier_id)
    active_record(session, Location, target_location_id)
    document = _document(session, payload, fingerprint, kind, user, 'MP-' if kind == 'putaway' else 'MT-')
    if document is None:
        return _existing(session, payload, fingerprint, (kind,), user)
    source_before = source.remaining_quantity
    source.remaining_quantity -= payload.quantity
    if movement is not None:
        movement.shelved_quantity += payload.quantity
    target, target_before = _target_batch(session, source, target_location_id, payload.quantity)
    session.add(MaterialTransfer(document_id=document.id, source_batch_id=source.id, target_batch_id=target.id,
                                 receipt_movement_id=movement.id if movement else None, quantity=payload.quantity))
    session.flush()
    _ledger(session, document, source, user, source_before, -payload.quantity, source.location_id, target_location_id)
    _ledger(session, document, target, user, target_before, payload.quantity, source.location_id, target_location_id)
    audit(session, user.id, kind, 'stock_documents', document.id,
          {'source_batch_id': source.id, 'target_batch_id': target.id, 'quantity': format(payload.quantity, 'f')})
    session.flush()
    return _result(session, document)


def putaway(session, user, payload):
    return _move(session, user, payload, 'putaway')


def transfer(session, user, payload):
    return _move(session, user, payload, 'transfer')


def count_stock(session, user, payload):
    require_permission(user, session, 'material.inventory.write')
    fingerprint = _fingerprint(payload)
    replay = _existing(session, payload, fingerprint, ('count_gain', 'count_loss', 'count_match'), user)
    if replay:
        return replay
    material = _material_for_batch(session, payload.batch_id)
    replay = _existing(session, payload, fingerprint, ('count_gain', 'count_loss', 'count_match'), user)
    if replay:
        return replay
    batch = session.scalar(select(MaterialBatch).where(MaterialBatch.id == payload.batch_id).with_for_update())
    if batch is None or batch.material_id != material.id or batch.stock_status != 'shelved' or batch.location_id is None:
        raise HTTPException(422, '只能盘点已上架的库位库存')
    if batch.remaining_quantity != payload.book_quantity:
        raise HTTPException(409, '账面库存已变化，请刷新后重新盘点')
    difference = payload.actual_quantity - batch.remaining_quantity
    kind = 'count_gain' if difference > 0 else 'count_loss' if difference < 0 else 'count_match'
    if difference > 0 and (batch.received_quantity + difference > MAX_QUANTITY or payload.actual_quantity > MAX_QUANTITY):
        raise HTTPException(422, '盘点后批次数量超出范围')
    document = _document(session, payload, fingerprint, kind, user, 'MC-')
    if document is None:
        return _existing(session, payload, fingerprint, ('count_gain', 'count_loss', 'count_match'), user)
    before = batch.remaining_quantity
    batch.remaining_quantity = payload.actual_quantity
    if difference > 0:
        batch.received_quantity += difference
    session.add(MaterialCount(document_id=document.id, batch_id=batch.id, book_quantity=before,
                              actual_quantity=payload.actual_quantity, difference=difference,
                              counted_by=user.id, counted_at=utcnow(), reason=payload.reason))
    session.flush()
    if difference:
        _ledger(session, document, batch, user, before, difference,
                batch.location_id if difference < 0 else None,
                batch.location_id if difference > 0 else None)
    audit(session, user.id, kind, 'stock_documents', document.id,
          {'batch_id': batch.id, 'book_quantity': format(before, 'f'),
           'actual_quantity': format(payload.actual_quantity, 'f'), 'reason': payload.reason})
    session.flush()
    return _result(session, document)


def adjust_stock(session, user, payload):
    require_permission(user, session, 'material.inventory.write')
    fingerprint = _fingerprint(payload)
    replay = _existing(session, payload, fingerprint, ('adjustment',), user)
    if replay:
        return replay
    material = _material_for_batch(session, payload.batch_id)
    replay = _existing(session, payload, fingerprint, ('adjustment',), user)
    if replay:
        return replay
    batch = session.scalar(select(MaterialBatch).where(MaterialBatch.id == payload.batch_id).with_for_update())
    if batch is None or batch.material_id != material.id or batch.stock_status != 'shelved' or batch.location_id is None:
        raise HTTPException(422, '只能调整已上架的库位库存')
    if batch.remaining_quantity != payload.book_quantity:
        raise HTTPException(409, '账面库存已变化，请刷新后重试')
    change = payload.change_quantity
    after = batch.remaining_quantity + change
    if change == 0 or after < 0 or after > MAX_QUANTITY or change > 0 and batch.received_quantity + change > MAX_QUANTITY:
        raise HTTPException(422, '调整数量无效或超出库存范围')
    document = _document(session, payload, fingerprint, 'adjustment', user, 'MA-')
    if document is None:
        return _existing(session, payload, fingerprint, ('adjustment',), user)
    before = batch.remaining_quantity
    batch.remaining_quantity = after
    if change > 0:
        batch.received_quantity += change
    session.add(MaterialAdjustment(document_id=document.id, batch_id=batch.id,
                                   before_quantity=before, change_quantity=change,
                                   after_quantity=after, operator_id=user.id, reason=payload.reason))
    session.flush()
    _ledger(session, document, batch, user, before, change,
            batch.location_id if change < 0 else None,
            batch.location_id if change > 0 else None)
    audit(session, user.id, 'adjustment', 'stock_documents', document.id,
          {'batch_id': batch.id, 'change_quantity': format(change, 'f'), 'reason': payload.reason})
    session.flush()
    return _result(session, document)
