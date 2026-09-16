"""原料记账：调用方持有请求事务；本模块只 flush，不单独 commit。"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import audit
from app.config import get_settings
from app.models import (RawMaterial, Supplier, Location, Role, MaterialBatch,
                        StockDocument, MaterialMovement, InventoryTransaction)
from app.unit_conversion import MAX_QUANTITY, convert_quantity

def business_today():
    return datetime.now(ZoneInfo(get_settings().business_timezone)).date()


def fefo_conditions(material_id: int):
    """唯一的 FEFO 可用批次口径；普通出库与生产领补料共同复用。"""
    return (
        MaterialBatch.material_id == material_id,
        MaterialBatch.remaining_quantity > 0,
        MaterialBatch.expiry_date >= business_today(),
        MaterialBatch.unit == RawMaterial.unit,
        RawMaterial.is_active.is_(True), RawMaterial.deleted_at.is_(None),
        Supplier.is_active.is_(True), Supplier.deleted_at.is_(None),
        Location.is_active.is_(True), Location.deleted_at.is_(None),
    )


def fefo_order():
    return (MaterialBatch.expiry_date, MaterialBatch.received_at, MaterialBatch.id)


def active_record(session, model, identity):
    record = session.scalar(select(model).where(model.id == identity).with_for_update())
    if record is None or not record.is_active or record.deleted_at:
        raise HTTPException(422, '原料、供应商或库位不存在或已停用')
    return record


def document_result(session, document):
    movement = session.scalar(select(MaterialMovement).where(MaterialMovement.document_id == document.id))
    transaction = session.scalar(select(InventoryTransaction).where(InventoryTransaction.document_id == document.id))
    return {'id': document.id, 'document_no': document.document_no, 'request_id': document.request_id,
            'kind': document.kind, 'batch_id': movement.batch_id, 'unit': transaction.unit,
            'quantity': format(movement.quantity, 'f'), 'before_quantity': format(transaction.before_quantity, 'f'),
            'after_quantity': format(transaction.after_quantity, 'f')}


def post_material(session, user, payload, kind):
    if session.get(Role, user.role_id).code not in ('admin', 'warehouse'):
        raise HTTPException(403, '仅管理员和仓库人员可以操作库存')
    content = payload.model_dump(mode='json', exclude={'request_id'})
    content['quantity'] = format(payload.quantity.normalize(), 'f')
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def replay(document):
        if document.kind != kind or document.operator_id != user.id or document.request_hash != fingerprint:
            raise HTTPException(409, '请求标识已使用且内容或操作者不同，请刷新后重试')
        return document_result(session, document)

    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing:
        return replay(existing)
    # 所有原料写入先锁原料：串行同原料的新批次创建、同批追加与出库；再锁关联档案/批次。
    material = active_record(session, RawMaterial, payload.material_id)
    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing:
        return replay(existing)
    quantity = convert_quantity(session, payload.quantity, payload.unit, material.unit)
    document = StockDocument(document_no='RM-' + uuid4().hex, request_id=payload.request_id,
                             request_hash=fingerprint, kind=kind, operator_id=user.id, notes=payload.notes)
    try:
        # 唯一键负责跨原料的同请求竞态；只回滚插入 savepoint，随后读取已提交结果。
        with session.begin_nested():
            session.add(document)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
        if existing:
            return replay(existing)
        raise
    if kind == 'material_receipt':
        today = business_today()
        if payload.production_date > today or payload.expiry_date < payload.production_date or payload.expiry_date < today:
            raise HTTPException(422, '生产日期不能晚于今天，到期日期不能早于生产日期或今天')
        active_record(session, Supplier, payload.supplier_id)
        active_record(session, Location, payload.location_id)
        batch = session.scalar(select(MaterialBatch).where(
            MaterialBatch.material_id == material.id, MaterialBatch.batch_no == payload.batch_no,
            MaterialBatch.supplier_id == payload.supplier_id, MaterialBatch.location_id == payload.location_id
        ).with_for_update())
        if batch:
            if (batch.unit, batch.production_date, batch.expiry_date) != (material.unit, payload.production_date, payload.expiry_date):
                raise HTTPException(409, '同批号、供应商及库位的单位或日期不一致')
            before = batch.remaining_quantity
            if batch.received_quantity + quantity > MAX_QUANTITY:
                raise HTTPException(422, '批次累计数量超出范围')
            batch.received_quantity += quantity
            batch.remaining_quantity += quantity
        else:
            before = Decimal('0')
            batch = MaterialBatch(material_id=material.id, batch_no=payload.batch_no,
                                  received_quantity=quantity, remaining_quantity=quantity, unit=material.unit,
                                  production_date=payload.production_date, expiry_date=payload.expiry_date,
                                  supplier_id=payload.supplier_id, location_id=payload.location_id, notes=payload.notes)
            session.add(batch)
        change = quantity
    else:
        batch = session.scalar(select(MaterialBatch).where(MaterialBatch.id == payload.batch_id).with_for_update())
        if not batch or batch.material_id != material.id:
            raise HTTPException(422, '批次不存在或不属于所选原料')
        if batch.unit != material.unit:
            raise HTTPException(409, '批次单位与原料单位不一致')
        active_record(session, Supplier, batch.supplier_id)
        active_record(session, Location, batch.location_id)
        if batch.expiry_date < business_today():
            raise HTTPException(422, '已过期批次禁止正常出库')
        before = batch.remaining_quantity
        if quantity > before:
            raise HTTPException(409, '批次库存不足')
        batch.remaining_quantity -= quantity
        change = -quantity
    session.flush()
    session.add(MaterialMovement(document_id=document.id, batch_id=batch.id, quantity=quantity,
                                 input_quantity=payload.quantity, input_unit=payload.unit))
    session.flush()
    session.add(InventoryTransaction(document_id=document.id, material_batch_id=batch.id, kind=kind,
                                    unit=batch.unit, before_quantity=before, change_quantity=change,
                                    after_quantity=batch.remaining_quantity, operator_id=user.id, notes=payload.notes))
    session.flush()
    audit(session, user.id, kind, 'stock_documents', document.id,
          {'batch_id': batch.id, 'quantity': format(quantity, 'f'), 'unit': batch.unit})
    session.flush()
    return document_result(session, document)
