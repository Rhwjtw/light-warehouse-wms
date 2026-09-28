"""Append-only whole-document inventory reversal in the request transaction."""
import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import audit, require_permission
from app.models import (FinishedBatch, FinishedOutbound, FinishedReceipt, FinishedProduct, InventoryTransaction,
                        MaterialAdjustment, MaterialTransfer,
                        Location, MaterialBatch, MaterialMovement, RawMaterial, StockDocument,
                        Supplier, User)
from app.stock_service import MAX_QUANTITY, business_today

from app.reversal_rules import SUPPORTED_KINDS, MOVES, complete_inverse, downstream_blockers, load_evidence
from collections import defaultdict


class AlreadyReversed(HTTPException):
    """Expected conflict, distinct from unexpected database failures."""
    code = 'ALREADY_REVERSED'

    def __init__(self):
        super().__init__(409, '原单已冲销，不能重复冲销')


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
            'transactions': rows,
            'inventory_scope': 'aggregate' if document.kind.startswith('finished_') or (document.reverses_kind or '').startswith('finished_') else 'material'}


class ReversalConflict(HTTPException):
    def __init__(self, code, message):
        self.code = code
        super().__init__(409, message)


def _require(condition, message, code='INVALID_ORIGINAL'):
    if not condition:
        raise ReversalConflict(code, message)


def _active(row):
    return row is not None and row.is_active and not row.deleted_at


def _plan(session, identity, *, lock=False, request_id=None):
    document = _document(session, identity)
    _require(document.kind in SUPPORTED_KINDS, '该业务不支持通用冲销', 'UNSUPPORTED_REVERSAL')
    original_rows = list(session.scalars(select(InventoryTransaction).where(
        InventoryTransaction.document_id == identity).order_by(InventoryTransaction.id)))
    raw = not document.kind.startswith('finished_')
    model = MaterialBatch if raw else FinishedBatch
    ids = [row.material_batch_id if raw else row.finished_batch_id for row in original_rows]
    _require(len(original_rows) == (2 if document.kind in MOVES else 1)
             and None not in ids and len(set(ids)) == len(ids)
             and all(row.kind == document.kind and row.unit for row in original_rows),
             '原单库存流水不完整，不能冲销；请先核查')
    owner_ids = set(session.scalars(select(model.material_id if raw else model.product_id).where(model.id.in_(ids))))
    _require(len(owner_ids) == 1, '原单库存归属不一致')
    owner_model = RawMaterial if raw else FinishedProduct
    query = select(owner_model).where(owner_model.id == next(iter(owner_ids))).execution_options(populate_existing=True)
    owner = session.scalar(query.with_for_update() if lock else query)
    _require(_active(owner), '原料或成品已停用，不能直接冲销')
    # All writers serialize by material/product. Multi-batch locks always use PK order.
    batches = {}
    for batch_id in sorted(ids):
        query = select(model).where(model.id == batch_id).execution_options(populate_existing=True)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is not None:
            batches[row.id] = row
    _require(len(batches) == len(ids), '原单库存行不存在')
    if lock and request_id:
        existing = session.scalar(select(StockDocument).where(StockDocument.request_id == request_id))
        if existing:
            return {'replay': existing}
    if _reversal_for(session, identity):
        raise AlreadyReversed()
    for batch in batches.values():
        _require(batch.unit == owner.unit, '库存单位与档案不一致')
        if raw:
            _require(_active(session.get(Supplier, batch.supplier_id)), '原批次供应商已停用')
            _require((batch.stock_status == 'pending' and batch.location_id is None)
                     or (batch.stock_status == 'shelved' and _active(session.get(Location, batch.location_id))),
                     '原批次状态或库位已变化')
        else:
            _require(batch.stock_scope == 'aggregate', '仅支持成品汇总库存数量冲销')
    movement = None
    shelved_after = None
    first = original_rows[0]
    batch = batches[ids[0]]
    evidence = load_evidence(session, ids) if raw else None
    if document.kind in ('material_receipt', 'material_outbound'):
        details = list(session.scalars(select(MaterialMovement).where(MaterialMovement.document_id == identity)
                                      .execution_options(populate_existing=True)))
        _require(len(details) == 1, '原单入出库明细不完整')
        movement = details[0]
        incoming = document.kind == 'material_receipt'
        _require(movement.batch_id == batch.id and movement.quantity == (first.change_quantity if incoming else -first.change_quantity)
                 and first.finished_batch_id is None, '原单明细与库存流水不一致')
        if incoming:
            _require(batch.stock_status == 'pending' and batch.location_id is None,
                     '仅支持待上架入库冲销，历史直接上架入库不适用', 'UNSUPPORTED_REVERSAL')
            _require(first.source_location_id is None and first.target_location_id is None, '待上架入库流水库位错误')
            _require(movement.shelved_quantity == 0, '该入库单已有有效上架业务，请先处理对应上架单', 'DOWNSTREAM_DEPENDENCY')
            _require(batch.remaining_quantity == movement.quantity, '待上架余额与原入库数量不一致')
            docs, ledger, _ = evidence
            by_doc = defaultdict(list)
            for row in ledger: by_doc[row.document_id].append(row)
            inverse = {d.reverses_document_id: d for d in docs.values() if d.kind == 'reversal'}
            for d in docs.values():
                if d.id <= identity or d.kind == 'reversal': continue
                _require(d.kind == 'putaway' and complete_inverse(d, inverse.get(d.id), by_doc),
                         '待上架库存存在尚未完整冲销的后续业务', 'DOWNSTREAM_DEPENDENCY')
        else:
            _require(batch.stock_status == 'shelved' and first.source_location_id == batch.location_id
                     and first.target_location_id is None, '原出库批次或库位不一致')
        movement = None  # Receipt cancellation does not rewrite original quantities/progress.
    elif document.kind in MOVES:
        transfer = session.scalar(select(MaterialTransfer).where(MaterialTransfer.document_id == identity))
        _require(transfer is not None and {transfer.source_batch_id, transfer.target_batch_id} == set(ids),
                 '原上架/移库明细与库存行不一致')
        source, target = batches[transfer.source_batch_id], batches[transfer.target_batch_id]
        _require(all(getattr(source, field) == getattr(target, field) for field in
                     ('material_id', 'batch_no', 'supplier_id', 'unit', 'production_date', 'expiry_date')),
                 '源目标批次属性不一致')
        _require(target.stock_status == 'shelved' and target.location_id is not None, '原目标不是库位库存')
        for row in original_rows:
            expected = -transfer.quantity if row.material_batch_id == source.id else transfer.quantity
            _require(row.change_quantity == expected and row.source_location_id == source.location_id
                     and row.target_location_id == target.location_id, '原上架/移库流水数量或方向不一致')
        if document.kind == 'putaway':
            # Ordinary SELECT refreshed after the material mutex; no fact-table locking read.
            movement = session.scalar(select(MaterialMovement).where(MaterialMovement.id == transfer.receipt_movement_id)
                                      .execution_options(populate_existing=True))
            receipt = session.get(StockDocument, movement.document_id) if movement else None
            _require(source.stock_status == 'pending' and source.location_id is None and movement is not None
                     and movement.batch_id == source.id and receipt is not None and receipt.kind == 'material_receipt'
                     and _reversal_for(session, receipt.id) is None, '原入库关联不完整或已冲销')
            _require(movement.shelved_quantity is not None
                     and source.remaining_quantity == movement.quantity - movement.shelved_quantity,
                     '待上架余额与上架进度不一致')
            shelved_after = movement.shelved_quantity - transfer.quantity
            _require(0 <= shelved_after <= movement.quantity, '冲销后上架进度越界')
        else:
            _require(source.stock_status == 'shelved' and transfer.receipt_movement_id is None,
                     '原移库来源状态不一致')
        blocked = downstream_blockers(document, source.id, target.id, *evidence)
        _require(not blocked, '存在有效下游业务，需先处理：' + '、'.join(d.document_no for d in blocked), 'DOWNSTREAM_DEPENDENCY')
    elif document.kind == 'adjustment':
        adjustment = session.scalar(select(MaterialAdjustment).where(MaterialAdjustment.document_id == identity))
        _require(adjustment is not None and adjustment.batch_id == batch.id and batch.stock_status == 'shelved'
                 and adjustment.change_quantity == first.change_quantity
                 and adjustment.before_quantity == first.before_quantity and adjustment.after_quantity == first.after_quantity
                 and first.source_location_id == (batch.location_id if first.change_quantity < 0 else None)
                 and first.target_location_id == (batch.location_id if first.change_quantity > 0 else None),
                 '原调整事实与流水不一致')
        blocked = downstream_blockers(document, batch.id, batch.id, *evidence)
        _require(not blocked, '该批次存在后续盘点边界，不能自动冲销调整', 'DOWNSTREAM_DEPENDENCY')
    else:
        incoming = document.kind == 'finished_receipt'
        detail_model = FinishedReceipt if incoming else FinishedOutbound
        details = list(session.scalars(select(detail_model).where(detail_model.document_id == identity)))
        _require(len(details) == 1, '成品原单明细不完整')
        detail = details[0]
        _require(document.business_type == 'normal' and (not incoming or detail.production_order_id is None),
                 '生产入库禁止通用冲销，仅支持普通汇总库存数量冲销', 'UNSUPPORTED_REVERSAL')
        _require(detail.batch_id == batch.id and detail.quantity == (first.change_quantity if incoming else -first.change_quantity)
                 and first.material_batch_id is None and first.source_location_id is None and first.target_location_id is None,
                 '成品原单明细与流水不一致')
    changes = []
    for row in original_rows:
        b = batches[row.material_batch_id if raw else row.finished_batch_id]
        before, change = b.remaining_quantity, -row.change_quantity
        after = before + change
        _require(after >= 0, '当前库存不足以整单冲销', 'INSUFFICIENT_STOCK')
        _require(after <= MAX_QUANTITY and (not raw or after <= b.received_quantity),
                 '冲销后余额超过可保存范围或历史累计流入量', 'STOCK_LIMIT')
        location = session.get(Location, b.location_id) if raw and b.location_id else None
        changes.append({'item_name': owner.name, 'batch_no': b.batch_no if raw else None,
                        'location_name': location.name if location else '待上架' if raw else '成品汇总库存',
                        'batch_id': b.id, 'unit': b.unit, 'before_quantity': format(before, 'f'),
                        'change_quantity': format(change, 'f'), 'after_quantity': format(after, 'f'),
                        'reverses_transaction_id': row.id})
    return {'document': document, 'rows': original_rows, 'batches': batches, 'changes': changes,
            'movement': movement, 'shelved_after': shelved_after}


def reversal_preview(session, identity):
    document = _document(session, identity)
    base = {'original_document_id': identity, 'original_document_no': document.document_no,
            'original_kind': document.kind, 'inventory_scope': 'aggregate' if document.kind.startswith('finished_') else 'material'}
    try:
        plan = _plan(session, identity)
        return {**base, 'eligible': True, 'code': 'OK',
                'message': ('成品汇总库存数量冲销，不识别具体实物批次；' if document.kind.startswith('finished_') else '')
                           + '仅支持整单冲销；提交时重新校验实时库存及有效下游业务',
                'changes': plan['changes'], 'shelved_quantity_after':
                    format(plan['shelved_after'], 'f') if plan['shelved_after'] is not None else None}
    except HTTPException as exc:
        if exc.status_code != 409: raise
        return {**base, 'eligible': False, 'code': getattr(exc, 'code', 'HTTP_ERROR'),
                'message': str(exc.detail), 'changes': []}


def reverse_document(session, user, identity, payload):
    require_permission(user, session, 'stock.reversal.write')
    fingerprint = hashlib.sha256(json.dumps({'original_document_id': identity, 'reason': payload.reason,
                                             'notes': payload.notes}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    def replay(existing):
        if (existing.kind != 'reversal' or existing.reverses_document_id != identity
                or existing.operator_id != user.id or existing.request_hash != fingerprint):
            raise ReversalConflict('REQUEST_CONFLICT', '请求标识已被其他业务使用')
        return document_detail(session, existing.id)
    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing: return replay(existing)
    plan = _plan(session, identity, lock=True, request_id=payload.request_id)
    if 'replay' in plan: return replay(plan['replay'])
    document = plan['document']
    reversal = StockDocument(document_no='RV-' + uuid4().hex, request_id=payload.request_id,
                             request_hash=fingerprint, business_date=business_today(),
                             business_type=document.business_type or 'normal', reference_no=document.document_no,
                             kind='reversal', reverses_document_id=document.id, reverses_kind=document.kind,
                             original_document_no_snapshot=document.document_no, reversal_reason=payload.reason,
                             operator_id=user.id, notes=payload.notes)
    try:
        with session.begin_nested():
            session.add(reversal)
            session.flush()
    except IntegrityError as exc:
        existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
        if existing: return replay(existing)
        if _reversal_for(session, identity) or 'uq_stock_documents_reverses_document_id' in str(exc.orig):
            raise AlreadyReversed() from None
        raise ReversalConflict('DATA_CONFLICT', '冲销单关联冲突，请刷新后核查') from None
    for change in plan['changes']:
        plan['batches'][change['batch_id']].remaining_quantity = Decimal(change['after_quantity'])
    session.flush()
    if plan['movement'] is not None:
        plan['movement'].shelved_quantity = plan['shelved_after']
        session.flush()
    for source, change in zip(plan['rows'], plan['changes']):
        session.add(InventoryTransaction(document_id=reversal.id, reverses_transaction_id=source.id,
                    material_batch_id=source.material_batch_id, finished_batch_id=source.finished_batch_id,
                    kind='reversal', unit=source.unit, before_quantity=Decimal(change['before_quantity']),
                    change_quantity=-source.change_quantity, after_quantity=Decimal(change['after_quantity']),
                    operator_id=user.id, source_location_id=source.target_location_id,
                    target_location_id=source.source_location_id, notes=payload.reason))
        session.flush()
    audit(session, user.id, 'reverse_stock_document', 'stock_documents', reversal.id,
          {'original_document_id': document.id, 'original_document_no': document.document_no,
           'original_kind': document.kind, 'reason': payload.reason, 'changes': plan['changes'],
           'change_quantity': plan['changes'][0]['change_quantity'] if len(plan['changes']) == 1 else None})
    session.flush()
    return document_detail(session, reversal.id)
