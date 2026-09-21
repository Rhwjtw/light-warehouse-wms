"""Read-only inventory/history audit. Run in one REPEATABLE READ snapshot.

V1 has no opening-balance import: every batch starts at zero. Never infer an
opening balance from the first ledger row, which could conceal missing entries.
"""
from collections import Counter, defaultdict
from decimal import Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import select

from app.models import (FinishedBatch, FinishedOutbound, FinishedReceipt,
                        InventoryTransaction, MaterialBatch, MaterialIssueRecord,
                        MaterialMovement, OperationLog, ProductionOrder,
                        ProductionOrderMaterial, StockDocument)
from app.settlement_service import loss_values
from app.unit_conversion import convert_quantity


def reconcile(session):
    # 本函数只读取并报告问题，不补流水、不改余额，也不把首条流水前量当作合法期初。
    problems = []
    def problem(kind, identity, reason):
        problems.append({'type': kind, 'id': identity, 'reason': reason})

    documents = {row.id: row for row in session.scalars(select(StockDocument))}
    ledger = list(session.scalars(select(InventoryTransaction).order_by(InventoryTransaction.id)))
    chains = defaultdict(list)
    actual = Counter()
    for row in ledger:
        scope = 'material' if row.material_batch_id is not None else 'finished'
        batch_id = row.material_batch_id if scope == 'material' else row.finished_batch_id
        chains[scope, batch_id].append(row)
        actual[row.document_id, scope, batch_id, row.kind, row.change_quantity] += 1
        doc = documents.get(row.document_id)
        if not doc or (doc.kind, doc.operator_id) != (row.kind, row.operator_id):
            problem('ledger', row.id, 'document kind/operator mismatch')

    counts = {}
    for scope, model in [('material', MaterialBatch), ('finished', FinishedBatch)]:
        batches = list(session.scalars(select(model)))
        counts[scope + '_batches'] = len(batches)
        for batch in batches:
            # V1 每个批次从零开始，按流水顺序重放，逐行验证连续性后再与当前余额比较。
            balance = Decimal('0')
            received = Decimal('0')
            for row in chains.pop((scope, batch.id), []):
                if row.before_quantity != balance or row.after_quantity != balance + row.change_quantity or row.unit != batch.unit:
                    problem(scope, batch.id, 'ledger chain/unit mismatch')
                balance += row.change_quantity
                if row.kind == 'material_receipt':
                    received += row.change_quantity
            if batch.remaining_quantity < 0 or balance != batch.remaining_quantity:
                problem(scope, batch.id, 'stock differs from ledger sum or is negative')
            if scope == 'material' and received != batch.received_quantity:
                problem(scope, batch.id, 'received quantity differs from receipt ledger')
    for scope, batch_id in chains:
        problem(scope, batch_id, 'ledger references missing batch')

    expected = Counter()
    # 由各类业务明细推导“应有流水”，再与真实流水逐键比对，发现缺失、重复或正负号错误。
    for model, scope in [(MaterialMovement, 'material'), (MaterialIssueRecord, 'material'),
                         (FinishedReceipt, 'finished'), (FinishedOutbound, 'finished')]:
        for row in session.scalars(select(model)):
            doc = documents.get(row.document_id)
            if doc is None:
                problem('detail', row.id, 'missing document')
                continue
            kind = row.kind if model is MaterialIssueRecord else doc.kind
            allowed = {MaterialMovement: ('material_receipt', 'material_outbound'),
                       MaterialIssueRecord: ('issue', 'supplement', 'return'),
                       FinishedReceipt: ('finished_receipt',), FinishedOutbound: ('finished_outbound',)}
            if doc.kind not in allowed[model]:
                problem('document', doc.id, 'wrong detail type')
            sign = 1 if kind in ('material_receipt', 'return', 'finished_receipt') else -1
            expected[row.document_id, scope, row.batch_id, kind, sign * row.quantity] += 1
    for key in expected.keys() | actual.keys():
        if expected[key] != actual[key]:
            problem('document', key[0], 'details and ledger differ')
    logged = {int(row.entity_id) for row in session.scalars(select(OperationLog).where(
        OperationLog.entity_type == 'stock_documents')) if row.entity_id and row.entity_id.isdigit()}
    posted = {key[0] for key in actual}
    for identity in documents:
        if identity not in posted or identity not in logged:
            problem('document', identity, 'missing ledger or audit')

    totals = defaultdict(lambda: Decimal('0'))
    for row in session.scalars(select(MaterialIssueRecord)):
        totals[row.order_material_id, row.kind] += row.quantity
    orders = {row.id: row for row in session.scalars(select(ProductionOrder))}
    receipts = defaultdict(list)
    for row in session.scalars(select(FinishedReceipt).where(FinishedReceipt.production_order_id.is_not(None))):
        receipts[row.production_order_id].append(row)
    for row in session.scalars(select(ProductionOrderMaterial)):
        quantities = tuple(totals[row.id, kind] for kind in ('issue', 'supplement', 'return'))
        if quantities != (row.issued_quantity, row.supplemented_quantity, row.returned_quantity):
            problem('production_material', row.id, 'posting totals differ')
        order = orders[row.production_order_id]
        if order.status in ('completed', 'received'):
            # 已完工订单用冻结的 BOM 快照和真实领补退记录重新计算理论量、实际量及损耗。
            used = quantities[0] + quantities[1] - quantities[2]
            if order.actual_quantity is None or order.actual_quantity <= 0:
                problem('production_order', order.id, 'invalid completed quantity')
                continue
            try:
                with localcontext() as precision:
                    precision.prec = 60
                    theoretical = convert_quantity(session, order.actual_quantity * row.quantity_per_unit, row.unit, row.base_unit)
            except HTTPException:
                problem('production_material', row.id, 'invalid historical conversion')
                continue
            loss, rate = loss_values(theoretical, used)
            if (row.final_theoretical_quantity, row.actual_used_quantity, row.loss_quantity, row.loss_rate) != (theoretical, used, loss, rate):
                problem('production_material', row.id, 'stored settlement differs from snapshot and postings')
    for order in orders.values():
        linked = receipts[order.id]
        if order.status in ('completed', 'received'):
            if len(linked) != 1 or linked[0].quantity != order.actual_quantity:
                problem('production_order', order.id, 'completion receipt missing/duplicated or quantity differs')
            for receipt in linked:
                batch = session.get(FinishedBatch, receipt.batch_id)
                doc = documents[receipt.document_id]
                if (batch.product_id, batch.unit, doc.business_type, doc.reference_no) != (order.product_id, order.unit, 'production', order.order_no):
                    problem('production_order', order.id, 'receipt product/unit/linkage differs')
        elif linked:
            problem('production_order', order.id, 'unfinished order has receipt')
    return {'ok': not problems, **counts, 'ledger_rows': len(ledger),
            'documents': len(documents), 'production_orders': len(orders), 'problems': problems}
