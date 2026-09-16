"""生产单、BOM 快照与领补退事务服务；调用方持有请求事务。"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, localcontext
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import audit, require_admin
from app.models import (
    BomHeader, BomItem, FinishedProduct, InventoryTransaction, Location, MaterialBatch,
    MaterialIssueRecord, ProductionOrder, ProductionOrderMaterial, RawMaterial, Role,
    StockDocument, Supplier, User, utcnow,
)
from app.stock_service import business_today, fefo_conditions, fefo_order
from app.unit_conversion import MAX_QUANTITY, convert_quantity, get_compatible_units


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), 'f')


def storable_quantity(value: Decimal, label: str) -> Decimal:
    if value <= 0 or value > MAX_QUANTITY or value != value.quantize(Decimal('0.000001')):
        raise HTTPException(422, f'{label}超出范围或超过六位小数')
    return value


def find_order(session, identity: int, *, lock: bool = False) -> ProductionOrder:
    query = select(ProductionOrder).where(ProductionOrder.id == identity)
    if lock:
        query = query.with_for_update()
    order = session.scalar(query)
    if order is None:
        raise HTTPException(404, '生产单不存在')
    return order


def material_rows(session, order_id: int):
    return session.scalars(
        select(ProductionOrderMaterial)
        .where(ProductionOrderMaterial.production_order_id == order_id)
        .order_by(ProductionOrderMaterial.id)
    ).all()


def material_data(row: ProductionOrderMaterial) -> dict:
    net = row.issued_quantity + row.supplemented_quantity - row.returned_quantity
    return {
        'id': row.id,
        'material_id': row.material_id,
        'material_code': row.material_code_snapshot,
        'material_name': row.material_name_snapshot,
        'quantity_per_unit': decimal_text(row.quantity_per_unit),
        'bom_unit': row.unit,
        'planned_required_quantity': decimal_text(row.planned_theoretical_quantity),
        'base_unit': row.base_unit,
        'issued_quantity': decimal_text(row.issued_quantity),
        'supplemented_quantity': decimal_text(row.supplemented_quantity),
        'returned_quantity': decimal_text(row.returned_quantity),
        'net_issued_quantity': decimal_text(net),
    }


def order_data(session, order: ProductionOrder, *, include_details: bool = True) -> dict:
    creator = session.get(User, order.created_by)
    result = {
        'id': order.id,
        'order_no': order.order_no,
        'finished_product_id': order.product_id,
        'finished_product_name': order.product_name_snapshot,
        'planned_quantity': decimal_text(order.planned_quantity),
        'finished_product_unit': order.unit,
        'bom_id': order.bom_id,
        'bom_version': order.bom_version,
        'status': order.status,
        'planned_date': order.planned_date.isoformat() if order.planned_date else None,
        'notes': order.notes,
        'created_by': order.created_by,
        'created_by_name': creator.display_name if creator else None,
        'created_at': order.created_at.isoformat(),
        'updated_at': order.updated_at.isoformat(),
    }
    if not include_details:
        return result
    result['materials'] = [material_data(row) for row in material_rows(session, order.id)]
    records = session.execute(
        select(MaterialIssueRecord, StockDocument, MaterialBatch, User, InventoryTransaction)
        .join(StockDocument, StockDocument.id == MaterialIssueRecord.document_id)
        .join(MaterialBatch, MaterialBatch.id == MaterialIssueRecord.batch_id)
        .join(User, User.id == StockDocument.operator_id)
        .join(InventoryTransaction,
              (InventoryTransaction.document_id == StockDocument.id)
              & (InventoryTransaction.material_batch_id == MaterialBatch.id))
        .join(ProductionOrderMaterial, ProductionOrderMaterial.id == MaterialIssueRecord.order_material_id)
        .where(ProductionOrderMaterial.production_order_id == order.id)
        .order_by(MaterialIssueRecord.id.desc())
    ).all()
    result['material_records'] = [{
        'id': record.id,
        'document_id': document.id,
        'document_no': document.document_no,
        'order_material_id': record.order_material_id,
        'batch_id': batch.id,
        'batch_no': batch.batch_no,
        'kind': record.kind,
        'quantity': decimal_text(record.quantity),
        'unit': batch.unit,
        'input_quantity': decimal_text(record.input_quantity),
        'input_unit': record.input_unit,
        'before_quantity': decimal_text(transaction.before_quantity),
        'change_quantity': decimal_text(transaction.change_quantity),
        'after_quantity': decimal_text(transaction.after_quantity),
        'operator_name': operator.display_name,
        'notes': document.notes,
        'created_at': document.created_at.isoformat(),
    } for record, document, batch, operator, transaction in records]
    return result


def create_order(session, user, payload) -> dict:
    require_admin(user, session)
    product = session.scalar(select(FinishedProduct).where(
        FinishedProduct.id == payload.finished_product_id,
        FinishedProduct.deleted_at.is_(None), FinishedProduct.is_active.is_(True),
    ).with_for_update())
    if product is None:
        raise HTTPException(422, '成品不存在、已停用或已删除')
    bom = session.scalar(select(BomHeader).where(
        BomHeader.product_id == product.id, BomHeader.status == 'active',
        BomHeader.deleted_at.is_(None),
    ).with_for_update())
    if bom is None:
        raise HTTPException(422, '该成品尚未配置有效BOM')
    if bom.output_unit != product.unit:
        raise HTTPException(422, '当前有效BOM的成品单位与成品档案不一致')
    items = session.execute(
        select(BomItem, RawMaterial).join(RawMaterial, RawMaterial.id == BomItem.material_id)
        .where(BomItem.bom_id == bom.id).order_by(BomItem.sort_order, BomItem.id)
    ).all()
    if not items:
        raise HTTPException(422, '当前有效BOM没有原材料明细')

    snapshots = []
    for item, material in items:
        if material.deleted_at is not None or not material.is_active:
            raise HTTPException(422, f'BOM原材料“{material.name}”不存在、已停用或已删除')
        get_compatible_units(session, item.unit, material.unit)
        with localcontext() as context:
            context.prec = 60
            bom_total = storable_quantity(item.quantity_per_unit * payload.planned_quantity, '计划理论需求')
        base_total = convert_quantity(session, bom_total, item.unit, material.unit)
        snapshots.append((item, material, base_total))

    order = ProductionOrder(
        order_no='TMP-' + uuid4().hex,
        product_id=product.id,
        product_name_snapshot=product.name,
        bom_id=bom.id,
        bom_version=bom.version,
        planned_quantity=payload.planned_quantity,
        unit=product.unit,
        status='ready',
        planned_date=payload.planned_date,
        created_by=user.id,
        notes=payload.notes,
    )
    session.add(order)
    session.flush()
    order.order_no = f'PO{business_today():%Y%m%d}{order.id:06d}'
    for item, material, base_total in snapshots:
        session.add(ProductionOrderMaterial(
            production_order_id=order.id,
            material_id=material.id,
            material_code_snapshot=material.code,
            material_name_snapshot=material.name,
            quantity_per_unit=item.quantity_per_unit,
            unit=item.unit,
            planned_theoretical_quantity=base_total,
            base_unit=material.unit,
            issued_quantity=Decimal('0'),
            supplemented_quantity=Decimal('0'),
            returned_quantity=Decimal('0'),
        ))
    session.flush()
    audit(session, user.id, 'create_production_order', 'production_orders', order.id, {
        'order_no': order.order_no, 'product_id': product.id, 'bom_id': bom.id,
        'bom_version': bom.version, 'planned_quantity': decimal_text(payload.planned_quantity),
        'unit': product.unit, 'material_count': len(snapshots),
    })
    session.flush()
    return order_data(session, order)


def update_order(session, user, identity: int, payload) -> dict:
    require_admin(user, session)
    order = find_order(session, identity, lock=True)
    if order.status in ('completed', 'received', 'cancelled'):
        raise HTTPException(409, '该生产单当前状态不允许修改')
    order.planned_date = payload.planned_date
    order.notes = payload.notes
    order.updated_at = utcnow()
    audit(session, user.id, 'update_production_order', 'production_orders', order.id,
          {'planned_date': payload.planned_date.isoformat() if payload.planned_date else None})
    session.flush()
    return order_data(session, order)


def cancel_order(session, user, identity: int) -> dict:
    require_admin(user, session)
    order = find_order(session, identity, lock=True)
    if order.status == 'cancelled':
        return order_data(session, order)
    if order.status in ('completed', 'received'):
        raise HTTPException(409, '已完成或已入库的生产单不能取消')
    count = session.scalar(
        select(func.count()).select_from(MaterialIssueRecord)
        .join(ProductionOrderMaterial)
        .where(ProductionOrderMaterial.production_order_id == order.id)
    )
    if count:
        raise HTTPException(409, '该生产单已发生物料业务，不能直接取消')
    order.status = 'cancelled'
    order.updated_at = utcnow()
    audit(session, user.id, 'cancel_production_order', 'production_orders', order.id,
          {'order_no': order.order_no})
    session.flush()
    return order_data(session, order)


def posting_result(session, document: StockDocument) -> dict:
    rows = session.execute(
        select(MaterialIssueRecord, MaterialBatch)
        .join(MaterialBatch, MaterialBatch.id == MaterialIssueRecord.batch_id)
        .where(MaterialIssueRecord.document_id == document.id)
        .order_by(MaterialIssueRecord.id)
    ).all()
    return {
        'id': document.id,
        'document_no': document.document_no,
        'request_id': document.request_id,
        'kind': document.kind,
        'production_order_no': document.reference_no,
        'allocations': [{
            'record_id': record.id,
            'order_material_id': record.order_material_id,
            'batch_id': batch.id,
            'batch_no': batch.batch_no,
            'quantity': decimal_text(record.quantity),
            'unit': batch.unit,
            'input_quantity': decimal_text(record.input_quantity),
            'input_unit': record.input_unit,
        } for record, batch in rows],
    }


def create_inventory_transaction(session, **values):
    """单独入口便于故障注入验证整个生产记账事务会回滚。"""
    transaction = InventoryTransaction(**values)
    session.add(transaction)
    session.flush()
    return transaction


def post_material(session, user, order_id: int, payload, kind: str) -> dict:
    role = session.get(Role, user.role_id)
    if role is None or role.code not in ('admin', 'warehouse'):
        raise HTTPException(403, '仅管理员和仓库人员可以办理生产领料、补料和退料')
    if kind not in ('issue', 'supplement', 'return'):
        raise ValueError('unsupported production material kind')
    batch_ids = [row.batch_id for row in payload.allocations]
    if len(batch_ids) != len(set(batch_ids)):
        raise HTTPException(422, '同一次操作不能重复选择同一批次')
    content = {
        'order_id': order_id,
        'order_material_id': payload.order_material_id,
        'unit': payload.unit,
        'allocations': [{'batch_id': row.batch_id, 'quantity': decimal_text(row.quantity)}
                        for row in payload.allocations],
        'notes': payload.notes,
    }
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def replay(document):
        if document.kind != kind or document.operator_id != user.id or document.request_hash != fingerprint:
            raise HTTPException(409, '请求标识已使用且内容或操作者不同，请刷新后重试')
        return posting_result(session, document)

    existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
    if existing:
        return replay(existing)

    order = find_order(session, order_id, lock=True)
    if order.status not in ('ready', 'in_progress'):
        raise HTTPException(409, '该生产单当前状态不能办理物料业务')
    order_material = session.scalar(select(ProductionOrderMaterial).where(
        ProductionOrderMaterial.id == payload.order_material_id,
        ProductionOrderMaterial.production_order_id == order.id,
    ).with_for_update())
    if order_material is None:
        raise HTTPException(422, '生产单原材料不存在')
    material = session.scalar(select(RawMaterial).where(RawMaterial.id == order_material.material_id).with_for_update())
    if material is None or material.deleted_at is not None or not material.is_active:
        raise HTTPException(422, '原材料不存在、已停用或已删除')
    if material.unit != order_material.base_unit:
        raise HTTPException(409, '原材料库存单位已改变，不能继续办理历史生产单')
    get_compatible_units(session, payload.unit, material.unit)

    batches = session.scalars(
        select(MaterialBatch).where(MaterialBatch.id.in_(sorted(batch_ids))).order_by(MaterialBatch.id).with_for_update()
    ).all()
    batch_map = {batch.id: batch for batch in batches}
    if len(batch_map) != len(batch_ids):
        raise HTTPException(422, '所选批次不存在')

    converted = []
    total = Decimal('0')
    for allocation in payload.allocations:
        batch = batch_map[allocation.batch_id]
        if batch.material_id != material.id:
            raise HTTPException(422, '所选批次不属于该生产单原材料')
        if batch.unit != material.unit:
            raise HTTPException(409, '批次单位与原材料库存单位不一致')
        quantity = convert_quantity(session, allocation.quantity, payload.unit, material.unit)
        total = storable_quantity(total + quantity, '本次物料数量')
        if kind in ('issue', 'supplement'):
            supplier = session.get(Supplier, batch.supplier_id)
            location = session.get(Location, batch.location_id)
            if (batch.expiry_date < business_today() or supplier is None or not supplier.is_active or supplier.deleted_at
                    or location is None or not location.is_active or location.deleted_at):
                raise HTTPException(422, '已过期或关联档案失效的批次禁止领料/补料')
            if quantity > batch.remaining_quantity:
                raise HTTPException(409, f'批次 {batch.batch_no} 库存不足')
        else:
            issued = session.scalar(select(func.coalesce(func.sum(MaterialIssueRecord.quantity), 0)).where(
                MaterialIssueRecord.order_material_id == order_material.id,
                MaterialIssueRecord.batch_id == batch.id,
                MaterialIssueRecord.kind.in_(('issue', 'supplement')),
            ))
            returned = session.scalar(select(func.coalesce(func.sum(MaterialIssueRecord.quantity), 0)).where(
                MaterialIssueRecord.order_material_id == order_material.id,
                MaterialIssueRecord.batch_id == batch.id,
                MaterialIssueRecord.kind == 'return',
            ))
            if quantity > issued - returned:
                raise HTTPException(409, f'批次 {batch.batch_no} 退料数量超过该生产单累计净领出量')
            if batch.remaining_quantity + quantity > batch.received_quantity:
                raise HTTPException(409, '退料后批次库存不能超过累计入库量')
        converted.append((allocation, batch, quantity))

    document = StockDocument(
        document_no={'issue': 'PI-', 'supplement': 'PS-', 'return': 'PR-'}[kind] + uuid4().hex,
        request_id=payload.request_id,
        request_hash=fingerprint,
        business_date=business_today(),
        business_type='production',
        reference_no=order.order_no,
        kind=kind,
        operator_id=user.id,
        notes=payload.notes,
    )
    try:
        with session.begin_nested():
            session.add(document)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(StockDocument).where(StockDocument.request_id == payload.request_id))
        if existing:
            return replay(existing)
        raise

    for allocation, batch, quantity in converted:
        before = batch.remaining_quantity
        change = quantity if kind == 'return' else -quantity
        batch.remaining_quantity += change
        session.flush()
        session.add(MaterialIssueRecord(
            document_id=document.id,
            order_material_id=order_material.id,
            batch_id=batch.id,
            kind=kind,
            quantity=quantity,
            input_quantity=allocation.quantity,
            input_unit=payload.unit,
        ))
        session.flush()
        create_inventory_transaction(
            session,
            document_id=document.id,
            material_batch_id=batch.id,
            kind=kind,
            unit=batch.unit,
            before_quantity=before,
            change_quantity=change,
            after_quantity=batch.remaining_quantity,
            operator_id=user.id,
            notes=payload.notes,
        )

    if kind == 'issue':
        order_material.issued_quantity = storable_quantity(order_material.issued_quantity + total, '累计领料数量')
    elif kind == 'supplement':
        order_material.supplemented_quantity = storable_quantity(order_material.supplemented_quantity + total, '累计补料数量')
    else:
        order_material.returned_quantity = storable_quantity(order_material.returned_quantity + total, '累计退料数量')
    order_material.updated_at = utcnow()
    if kind in ('issue', 'supplement') and order.status == 'ready':
        order.status = 'in_progress'
        order.updated_at = utcnow()
    session.flush()
    audit(session, user.id, f'production_{kind}', 'stock_documents', document.id, {
        'production_order_id': order.id,
        'order_material_id': order_material.id,
        'quantity': decimal_text(total),
        'unit': material.unit,
        'batch_count': len(converted),
    })
    session.flush()
    return posting_result(session, document)


def fefo_suggestion(session, order_id: int, material_id: int, requested_quantity: Decimal | None = None) -> dict:
    order = find_order(session, order_id)
    if order.status not in ('ready', 'in_progress'):
        raise HTTPException(409, '该生产单当前状态不能推荐领料批次')
    order_material = session.scalar(select(ProductionOrderMaterial).where(
        ProductionOrderMaterial.production_order_id == order.id,
        ProductionOrderMaterial.material_id == material_id,
    ))
    if order_material is None:
        raise HTTPException(404, '生产单中没有该原材料')
    remaining = max(order_material.planned_theoretical_quantity - order_material.issued_quantity, Decimal('0'))
    target = requested_quantity if requested_quantity is not None else remaining
    if target < 0 or target > MAX_QUANTITY:
        raise HTTPException(422, '建议数量超出范围')
    rows = session.scalars(
        select(MaterialBatch)
        .join(RawMaterial, RawMaterial.id == MaterialBatch.material_id)
        .join(Supplier, Supplier.id == MaterialBatch.supplier_id)
        .join(Location, Location.id == MaterialBatch.location_id)
        .where(*fefo_conditions(material_id)).order_by(*fefo_order())
    ).all()
    left = target
    suggestions = []
    batches = []
    available = Decimal('0')
    for batch in rows:
        available += batch.remaining_quantity
        suggested = min(left, batch.remaining_quantity) if left > 0 else Decimal('0')
        if suggested > 0:
            suggestions.append({'batch_id': batch.id, 'batch_no': batch.batch_no,
                                'quantity': decimal_text(suggested), 'unit': batch.unit})
            left -= suggested
        batches.append({
            'id': batch.id,
            'batch_no': batch.batch_no,
            'remaining_quantity': decimal_text(batch.remaining_quantity),
            'unit': batch.unit,
            'expiry_date': batch.expiry_date.isoformat(),
            'received_at': batch.received_at.isoformat(),
        })
    return {
        'production_order_id': order.id,
        'order_material_id': order_material.id,
        'material_id': material_id,
        'material_name': order_material.material_name_snapshot,
        'planned_required_quantity': decimal_text(order_material.planned_theoretical_quantity),
        'issued_quantity': decimal_text(order_material.issued_quantity),
        'remaining_to_issue': decimal_text(remaining),
        'requested_quantity': decimal_text(target),
        'base_unit': order_material.base_unit,
        'available_quantity': decimal_text(available),
        'fully_satisfied': left == 0,
        'suggestions': suggestions,
        'batches': batches,
    }
