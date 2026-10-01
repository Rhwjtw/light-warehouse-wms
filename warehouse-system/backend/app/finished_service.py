"""成品普通记账。复用统一单据、流水和函数作用域事务；此处不 commit。"""
import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import audit, require_permission
from app.models import (FinishedProduct, FinishedBatch, FinishedReceipt, FinishedOutbound,
                        StockDocument, InventoryTransaction)
from app.stock_service import MAX_QUANTITY, business_today


def result_for(session, document):
    transactions = list(session.scalars(select(InventoryTransaction).where(
        InventoryTransaction.document_id == document.id).order_by(InventoryTransaction.id)))
    transaction = transactions[0] if transactions else None
    if transaction is None or transaction.finished_batch_id is None:
        raise HTTPException(409, '单据没有完整的成品流水，请联系管理员核查')
    batch = session.get(FinishedBatch, transaction.finished_batch_id)
    return {'id': document.id, 'document_no': document.document_no, 'request_id': document.request_id,
            'product_id': batch.product_id, 'kind': document.kind, 'business_type': document.business_type,
            'business_date': document.business_date.isoformat(), 'reference_no': document.reference_no,
            'unit': transaction.unit, 'quantity': format(sum((abs(t.change_quantity) for t in transactions), Decimal('0')), 'f'),
            'change_quantity': format(sum((t.change_quantity for t in transactions), Decimal('0')), 'f'),
            'before_quantity': format(sum((t.before_quantity for t in transactions), Decimal('0')), 'f'),
            'after_quantity': format(sum((t.after_quantity for t in transactions), Decimal('0')), 'f'),
            'allocations': [{'batch_id': t.finished_batch_id,
                             'batch_no': session.get(FinishedBatch, t.finished_batch_id).batch_no,
                             'stock_scope': session.get(FinishedBatch, t.finished_batch_id).stock_scope,
                             'quantity': format(abs(t.change_quantity), 'f')} for t in transactions]}


def post_finished(session, user, payload, kind, *, production_order=None, production_batches=None):
    require_permission(user, session, 'production.manage' if production_order is not None else 'finished.inventory.write')
    if production_order is None and payload.business_type != 'normal':
        # 普通接口不能伪造生产入库；生产入库只能由完工结算传入锁定后的生产单。
        raise HTTPException(422, '生产入库必须通过生产单完成操作')
    if production_order is not None and (
        kind != 'finished_receipt' or payload.business_type != 'production'
        or payload.product_id != production_order.product_id or payload.unit != production_order.unit
        or payload.quantity != production_order.actual_quantity
    ):
        raise HTTPException(422, '生产入库与生产单不一致')
    if payload.business_date > business_today():
        raise HTTPException(422, '入出库日期不能晚于业务今天')
    content = payload.model_dump(mode='json', exclude={'request_id'})
    if payload.allocations is None:
        # Preserve 0013 normal-document retry fingerprints when no new field was supplied.
        content.pop('allocations', None)
    # 数量规范化后参与指纹，确保“1”和“1.0”被视为同一业务请求，同时仍拒绝内容冲突。
    content['quantity'] = format(payload.quantity.normalize(), 'f')
    if payload.allocations:
        content['allocations'] = sorted([{'batch_id': a.batch_id, 'quantity': format(a.quantity.normalize(), 'f')}
                                         for a in payload.allocations], key=lambda a: a['batch_id'])
    if production_batches:
        content['finished_batches'] = [b.model_dump(mode='json') for b in production_batches]
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def replay(document):
        if (document.kind, document.operator_id, document.request_hash) != (kind, user.id, fingerprint):
            raise HTTPException(409, '请求标识已使用且内容或操作者不同')
        return result_for(session, document)

    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing:
        return replay(existing)
    # 先锁成品档案，串行该成品的首次建库存、追加、扣减；档案改单位也受同一行锁保护。
    product = session.scalar(select(FinishedProduct).where(FinishedProduct.id == payload.product_id).with_for_update())
    if not product or not product.is_active or product.deleted_at:
        raise HTTPException(422, '成品不存在或已停用')
    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing:
        return replay(existing)
    if payload.unit != product.unit:
        raise HTTPException(422, '单位必须与成品档案一致，不自动换算成品单位')
    batches = list(session.scalars(select(FinishedBatch).where(FinishedBatch.product_id == product.id)
                                  .order_by(FinishedBatch.id).with_for_update()))
    if any(batch.stock_scope not in ('aggregate', 'batch') for batch in batches):
        raise HTTPException(409, '存在旧库位批次，归属尚未确认，禁止自动转换')
    if any(batch.unit != product.unit for batch in batches):
        raise HTTPException(409, '库存单位与成品档案不一致')
    incoming = kind == 'finished_receipt'
    postings = []
    if production_order is not None:
        from app.schemas import FinishedProductionBatchInput
        # 兼容旧调用方：缺省时明确创建以生产单号命名的一个新真实批次，不使用历史 aggregate。
        specifications = production_batches or [FinishedProductionBatchInput(batch_no=production_order.order_no, quantity=payload.quantity)]
        if sum((b.quantity for b in specifications), Decimal('0')) != payload.quantity:
            raise HTTPException(422, '成品批次数量合计必须等于实际产量')
        # 大小写/尾空格由 MySQL 唯一索引最终裁决；任一重复会回滚整个完成事务。
        if len({b.batch_no for b in specifications}) != len(specifications):
            raise HTTPException(422, '成品批次号重复')
        for spec in specifications:
            if spec.batch_no == '__aggregate__' or (spec.expiry_date and spec.expiry_date < payload.business_date):
                raise HTTPException(422, '批次号保留或到期日期早于生产日期')
            if session.scalar(select(FinishedBatch.id).where(FinishedBatch.product_id == product.id,
                                                           FinishedBatch.stock_scope == 'batch', FinishedBatch.batch_no == spec.batch_no)):
                raise HTTPException(409, '同一成品的真实批次号已存在')
            batch = FinishedBatch(product_id=product.id, batch_no=spec.batch_no, stock_scope='batch', unit=product.unit,
                                  production_date=payload.business_date, expiry_date=spec.expiry_date, remaining_quantity=Decimal('0'))
            postings.append((batch, spec.quantity))
    elif incoming:
        if payload.allocations:
            raise HTTPException(422, '普通入库不能伪造生产批次；请通过生产完成创建批次')
        batch = next((b for b in batches if b.stock_scope == 'aggregate'), None)
        if batch is None:
            batch = FinishedBatch(product_id=product.id, batch_no='__aggregate__', stock_scope='aggregate', unit=product.unit,
                                  remaining_quantity=Decimal('0'))
        postings = [(batch, payload.quantity)]
    else:
        if payload.allocations:
            if len({a.batch_id for a in payload.allocations}) != len(payload.allocations):
                raise HTTPException(422, '出库批次不能重复')
            if sum((a.quantity for a in payload.allocations), Decimal('0')) != payload.quantity:
                raise HTTPException(422, '各批次出库合计必须等于单据数量')
            by_id = {b.id: b for b in batches}
            for a in sorted(payload.allocations, key=lambda a: a.batch_id):
                if a.batch_id not in by_id:
                    raise HTTPException(422, '出库批次不属于所选成品')
                postings.append((by_id[a.batch_id], a.quantity))
        else:
            if any(b.stock_scope == 'batch' and b.remaining_quantity > 0 for b in batches):
                raise HTTPException(422, '存在真实批次库存，必须明确选择出库批次或历史无批次库存')
            aggregate = next((b for b in batches if b.stock_scope == 'aggregate'), None)
            if aggregate is None:
                raise HTTPException(409, '成品库存不足')
            postings = [(aggregate, payload.quantity)]
    for batch, quantity in postings:
        after = batch.remaining_quantity + (quantity if incoming else -quantity)
        if after < 0:
            raise HTTPException(409, '所选成品批次库存不足')
        if after > MAX_QUANTITY:
            raise HTTPException(422, '成品库存超出可保存的数量上限')
    document = StockDocument(document_no='FG-' + uuid4().hex, request_id=payload.request_id,
                             request_hash=fingerprint, kind=kind, operator_id=user.id,
                             business_date=payload.business_date, business_type=payload.business_type,
                             reference_no=payload.reference_no, notes=payload.notes)
    try:
        # savepoint 只处理 request_id 唯一键竞态；外层事务仍覆盖库存、明细、流水和审计的原子性。
        with session.begin_nested():
            session.add(document)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
        if existing:
            return replay(existing)
        raise
    for batch, quantity in postings:
        before = batch.remaining_quantity
        change = quantity if incoming else -quantity
        batch.remaining_quantity = before + change
        session.add(batch)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(409, '成品批次重复或批次数据约束不满足') from exc
        movement = FinishedReceipt if incoming else FinishedOutbound
        linkage = {'production_order_id': production_order.id} if production_order is not None else {}
        session.add(movement(document_id=document.id, batch_id=batch.id, quantity=quantity, **linkage))
        session.add(InventoryTransaction(document_id=document.id, finished_batch_id=batch.id, kind=kind,
                                        unit=product.unit, before_quantity=before, change_quantity=change,
                                        after_quantity=batch.remaining_quantity, operator_id=user.id, notes=payload.notes))
    session.flush()
    audit(session, user.id, kind, 'stock_documents', document.id,
          {'product_id': product.id, 'quantity': format(payload.quantity, 'f'), 'unit': product.unit})
    session.flush()
    return result_for(session, document)
