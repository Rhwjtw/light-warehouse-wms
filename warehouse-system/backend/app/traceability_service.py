"""Read-only factual traceability. Never allocate input usage to output sub-batches."""
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import Engine, select, or_, func
from sqlalchemy.orm import Session

from app.models import (MaterialBatch, RawMaterial, Supplier, Location, MaterialIssueRecord,
                        ProductionOrderMaterial, ProductionOrder, FinishedBatch, FinishedProduct,
                        FinishedReceipt, InventoryTransaction, StockDocument)
from app.reversal_rules import reversed_document_ids

LOGICAL_FIELDS = ('material_id', 'batch_no', 'supplier_id', 'production_date', 'expiry_date', 'unit')


@contextmanager
def read_snapshot(session):
    """Engine-backed API reads use one snapshot; externally bound callers own their transaction."""
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        yield session
        return
    with bind.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin(), Session(bind=connection) as snapshot:
            yield snapshot


def logical_key(batch):
    # Dates compare as DATE, NULL stays NULL. Do not trim/case-fold historical labels or
    # convert historical units: that would silently merge distinct business identities.
    return tuple(getattr(batch, field) for field in LOGICAL_FIELDS)


def logical_rows(session, batch):
    rows = session.scalars(select(MaterialBatch).where(*(
        getattr(MaterialBatch, field) == getattr(batch, field) for field in LOGICAL_FIELDS)).order_by(MaterialBatch.id))
    # MySQL collation may ignore case/trailing spaces; equality here preserves exact facts.
    return [r for r in rows if logical_key(r) == logical_key(batch)]


def material_info(session, batch, rows=None):
    rows = rows if rows is not None else logical_rows(session, batch)
    material = session.get(RawMaterial, batch.material_id)
    supplier = session.get(Supplier, batch.supplier_id) if batch.supplier_id else None
    return {'id': min(r.id for r in rows), 'locator_id': batch.id, 'physical_batch_ids': [r.id for r in rows],
            'material_id': batch.material_id, 'material_name': material.name if material else '历史原料关联缺失',
            'batch_no': batch.batch_no, 'supplier_id': batch.supplier_id,
            'supplier_name': supplier.name if supplier else '历史供应商无法确定',
            'production_date': batch.production_date, 'expiry_date': batch.expiry_date, 'unit': batch.unit,
            'current_quantity': format(sum((r.remaining_quantity for r in rows), Decimal('0')), 'f'),
            'locations': [{'batch_id': r.id, 'stock_status': r.stock_status, 'quantity': format(r.remaining_quantity, 'f'),
                           'location_name': session.get(Location, r.location_id).name if r.location_id else '待上架'} for r in rows]}


def finished_info(session, batch, quantity=None):
    product = session.get(FinishedProduct, batch.product_id)
    return {'id': batch.id, 'product_id': batch.product_id, 'product_name': product.name if product else None,
            'batch_no': batch.batch_no if batch.stock_scope == 'batch' else None, 'stock_scope': batch.stock_scope,
            'label': batch.batch_no if batch.stock_scope == 'batch' else '历史未分批生产入库',
            'production_date': batch.production_date, 'expiry_date': batch.expiry_date, 'unit': batch.unit,
            'production_quantity': format(quantity, 'f') if quantity is not None else None,
            'current_quantity': format(batch.remaining_quantity, 'f')}


def document_summaries(session, ids):
    ids = set(ids)
    if not ids:
        return []
    docs = list(session.scalars(select(StockDocument).where(
        StockDocument.id.in_(ids) | StockDocument.reverses_document_id.in_(ids)).order_by(StockDocument.id)))
    cancelled = reversed_document_ids(session, ids)
    inverse = {d.reverses_document_id: d.id for d in docs if d.kind == 'reversal'}
    return [{'id': d.id, 'document_no': d.document_no, 'kind': d.kind, 'reference_no': d.reference_no,
             'created_at': d.created_at, 'status': 'reversal' if d.kind == 'reversal' else
             'reversed' if d.id in cancelled else 'incomplete_reversal' if d.id in inverse else 'normal',
             'reversal_document_id': inverse.get(d.id), 'reverses_document_id': d.reverses_document_id,
             'reversal_reason': d.reversal_reason} for d in docs]


def order_trace(session, identity):
    order = session.get(ProductionOrder, identity)
    if order is None:
        raise HTTPException(404, '生产单不存在')
    snapshots = list(session.scalars(select(ProductionOrderMaterial).where(ProductionOrderMaterial.production_order_id == identity)))
    records = list(session.scalars(select(MaterialIssueRecord).where(MaterialIssueRecord.order_material_id.in_([r.id for r in snapshots]))))
    cancelled = reversed_document_ids(session, [r.document_id for r in records])
    groups, doc_ids = {}, {r.document_id for r in records}
    for record in records:
        batch = session.get(MaterialBatch, record.batch_id)
        if batch is None:
            continue
        key = logical_key(batch)
        if key not in groups:
            groups[key] = {**material_info(session, batch), 'issued_quantity': Decimal('0'),
                           'supplemented_quantity': Decimal('0'), 'returned_quantity': Decimal('0')}
        if record.document_id not in cancelled:
            field = {'issue': 'issued_quantity', 'supplement': 'supplemented_quantity', 'return': 'returned_quantity'}[record.kind]
            groups[key][field] += record.quantity
    for g in groups.values():
        g['net_used_quantity'] = g['issued_quantity'] + g['supplemented_quantity'] - g['returned_quantity']
        g['effective'] = g['net_used_quantity'] > 0
        for field in ('issued_quantity', 'supplemented_quantity', 'returned_quantity', 'net_used_quantity'):
            g[field] = format(g[field], 'f')
        doc_ids.update(session.scalars(select(InventoryTransaction.document_id).where(InventoryTransaction.material_batch_id.in_(g['physical_batch_ids']))))
    receipts = list(session.scalars(select(FinishedReceipt).where(FinishedReceipt.production_order_id == identity)))
    cancelled_receipts = reversed_document_ids(session, [r.document_id for r in receipts])
    outputs, warnings = [], ['原材料实际净使用量是生产单级事实；未记录分批投料，不将其平均或按比例分摊到成品子批次。']
    for receipt in receipts:
        doc_ids.add(receipt.document_id)
        batch = session.get(FinishedBatch, receipt.batch_id)
        if receipt.document_id in cancelled_receipts or batch is None:
            continue
        outputs.append(finished_info(session, batch, receipt.quantity))
        if batch.stock_scope == 'batch':
            doc_ids.update(session.scalars(select(InventoryTransaction.document_id).where(InventoryTransaction.finished_batch_id == batch.id)))
        else:
            outputs[-1]['current_quantity'] = None
            warnings.append('历史成品批次无法确定；该次生产入库数量可查，但当前 aggregate 库存及历史出库不能归属到这张生产单。')
    for g in groups.values():
        receipt_docs = [d for d in document_summaries(session, set(session.scalars(select(InventoryTransaction.document_id).where(
            InventoryTransaction.material_batch_id.in_(g['physical_batch_ids']))))) if d['kind'] == 'material_receipt']
        g['receipt_documents'] = receipt_docs
        if len([d for d in receipt_docs if d['status'] != 'reversed']) > 1:
            warnings.append('该逻辑原料批次涉及多次入库，无法准确分摊某次领料属于具体哪张入库单。')
    return {'id': order.id, 'order_no': order.order_no, 'status': order.status, 'product_name': order.product_name_snapshot,
            'actual_quantity': format(order.actual_quantity, 'f') if order.actual_quantity is not None else None,
            'unit': order.unit, 'completed_at': order.completed_at, 'materials': list(groups.values()),
            'finished_batches': outputs, 'documents': document_summaries(session, doc_ids), 'warnings': sorted(set(warnings))}


def material_trace(session, identity):
    batch = session.get(MaterialBatch, identity)
    if batch is None:
        raise HTTPException(404, '原材料批次不存在')
    rows = logical_rows(session, batch)
    ids = [r.id for r in rows]
    orders = set(session.scalars(select(ProductionOrderMaterial.production_order_id).join(
        MaterialIssueRecord, MaterialIssueRecord.order_material_id == ProductionOrderMaterial.id).where(MaterialIssueRecord.batch_id.in_(ids))))
    effective, audit = [], []
    for order_id in sorted(orders):
        trace = order_trace(session, order_id)
        use = next((r for r in trace['materials'] if set(r['physical_batch_ids']) & set(ids)), None)
        if use and use['effective']:
            trace['selected_material_usage'] = use
            effective.append(trace)
        else:
            audit.append({'id': trace['id'], 'order_no': trace['order_no'], 'net_used_quantity': use['net_used_quantity'] if use else '0',
                         'reason': '已冲销或净使用为零，不作为有效生产关系'})
    docs = set(session.scalars(select(InventoryTransaction.document_id).where(InventoryTransaction.material_batch_id.in_(ids))))
    for trace in effective:
        docs.update(d['id'] for d in trace['documents'])
    warnings = sorted({w for t in effective for w in t['warnings']})
    return {'entry_type': 'material', 'subject': material_info(session, batch, rows), 'production_orders': effective,
            'excluded_orders': audit, 'documents': document_summaries(session, docs), 'warnings': warnings}


def finished_trace(session, identity):
    batch = session.get(FinishedBatch, identity)
    if batch is None:
        raise HTTPException(404, '成品批次不存在')
    docs = set(session.scalars(select(InventoryTransaction.document_id).where(InventoryTransaction.finished_batch_id == identity)))
    receipts = list(session.scalars(select(FinishedReceipt).where(FinishedReceipt.batch_id == identity)))
    cancelled = reversed_document_ids(session, [r.document_id for r in receipts])
    warnings, orders = [], []
    if batch.stock_scope != 'batch':
        warnings.append('历史无批次归属库存：历史成品批次、生产来源及出库批次归属无法确定；不会反推或分摊。')
    else:
        order_ids = {r.production_order_id for r in receipts if r.production_order_id and r.document_id not in cancelled}
        orders = [order_trace(session, key) for key in sorted(order_ids)]
        if not orders:
            warnings.append('历史数据缺少生产关联，无法确定实际使用原料。')
        for trace in orders:
            docs.update(d['id'] for d in trace['documents'])
            warnings.extend(trace['warnings'])
    quantity = sum((r.quantity for r in receipts if r.document_id not in cancelled), Decimal('0')) if batch.stock_scope == 'batch' else None
    return {'entry_type': 'finished', 'subject': finished_info(session, batch, quantity), 'production_orders': orders,
            'documents': document_summaries(session, docs), 'warnings': sorted(set(warnings))}


def search(session, query, limit):
    # Group binary strings: old NULL dates remain distinct from known dates, and SQL
    # collation cannot merge supplier/date/unit variants into a misleading candidate.
    fields = [MaterialBatch.material_id, func.binary(MaterialBatch.batch_no), MaterialBatch.supplier_id,
              MaterialBatch.production_date, MaterialBatch.expiry_date, func.binary(MaterialBatch.unit)]
    found = session.scalars(select(func.min(MaterialBatch.id)).join(RawMaterial).outerjoin(Supplier).where(or_(
        MaterialBatch.batch_no.contains(query, autoescape=True), RawMaterial.name.contains(query, autoescape=True),
        RawMaterial.code.contains(query, autoescape=True), Supplier.name.contains(query, autoescape=True))).group_by(*fields).limit(limit + 1)).all()
    products = session.scalars(select(FinishedBatch).join(FinishedProduct).where(or_(
        FinishedBatch.batch_no.contains(query, autoescape=True), FinishedProduct.name.contains(query, autoescape=True),
        FinishedProduct.code.contains(query, autoescape=True))).order_by(FinishedBatch.id).limit(limit + 1)).all()
    orders = session.scalars(select(ProductionOrder).where(ProductionOrder.order_no.contains(query, autoescape=True)).order_by(ProductionOrder.id).limit(limit + 1)).all()
    items = [{'entry_type': 'material', **material_info(session, session.get(MaterialBatch, key))} for key in found[:limit]]
    items.extend({'entry_type': 'finished', **finished_info(session, row)} for row in products[:limit])
    items.extend({'entry_type': 'production', 'id': row.id, 'order_no': row.order_no, 'product_name': row.product_name_snapshot} for row in orders[:limit])
    return {'items': items, 'truncated': any(len(rows) > limit for rows in (found, products, orders)), 'limit_per_type': limit}
