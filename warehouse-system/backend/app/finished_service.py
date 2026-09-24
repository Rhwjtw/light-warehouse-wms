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
    transaction = session.scalar(select(InventoryTransaction).where(InventoryTransaction.document_id == document.id))
    if transaction is None or transaction.finished_batch_id is None:
        raise HTTPException(409, '单据没有完整的成品流水，请联系管理员核查')
    batch = session.get(FinishedBatch, transaction.finished_batch_id)
    return {'id': document.id, 'document_no': document.document_no, 'request_id': document.request_id,
            'product_id': batch.product_id, 'kind': document.kind, 'business_type': document.business_type,
            'business_date': document.business_date.isoformat(), 'reference_no': document.reference_no,
            'unit': transaction.unit, 'quantity': format(abs(transaction.change_quantity), 'f'),
            'before_quantity': format(transaction.before_quantity, 'f'),
            'after_quantity': format(transaction.after_quantity, 'f')}


def post_finished(session, user, payload, kind, *, production_order=None):
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
    # 数量规范化后参与指纹，确保“1”和“1.0”被视为同一业务请求，同时仍拒绝内容冲突。
    content['quantity'] = format(payload.quantity.normalize(), 'f')
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
    # 历史批次结构保留，不能偷偷合并或忽略非汇总库存；当前实库升级前没有成品批次。
    if any(batch.stock_scope != 'aggregate' for batch in batches):
        raise HTTPException(409, '该成品存在批次记录，需先制定批次转换方案')
    batch = batches[0] if batches else None
    before = batch.remaining_quantity if batch else Decimal('0')
    if batch and batch.unit != product.unit:
        raise HTTPException(409, '库存单位与成品档案不一致')
    incoming = kind == 'finished_receipt'
    if not incoming and payload.quantity > before:
        raise HTTPException(409, '成品库存不足')
    change = payload.quantity if incoming else -payload.quantity
    after = before + change
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
    if batch is None:
        # 首次记账创建每成品唯一的汇总库存行；后续普通入库和生产入库都累加到同一余额。
        batch = FinishedBatch(product_id=product.id, batch_no='__aggregate__', stock_scope='aggregate',
                              unit=product.unit, location_id=None, remaining_quantity=after)
        session.add(batch)
    else:
        batch.remaining_quantity = after
    session.flush()
    movement = FinishedReceipt if incoming else FinishedOutbound
    # production_order_id 在既有 FinishedReceipt 中保留；普通接口永不写入生产单关联。
    linkage = {'production_order_id': production_order.id} if production_order is not None else {}
    session.add(movement(document_id=document.id, batch_id=batch.id, quantity=payload.quantity, **linkage))
    session.flush()
    session.add(InventoryTransaction(document_id=document.id, finished_batch_id=batch.id, kind=kind,
                                    unit=product.unit, before_quantity=before, change_quantity=change,
                                    after_quantity=after, operator_id=user.id, notes=payload.notes))
    session.flush()
    audit(session, user.id, kind, 'stock_documents', document.id,
          {'product_id': product.id, 'quantity': format(payload.quantity, 'f'), 'unit': product.unit})
    session.flush()
    return result_for(session, document)
