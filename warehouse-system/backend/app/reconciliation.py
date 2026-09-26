"""Read-only inventory/history audit. Run in one REPEATABLE READ snapshot.

V1 has no opening-balance import: every batch starts at zero. Never infer an
opening balance from the first ledger row, which could conceal missing entries.
"""
from collections import Counter, defaultdict
from decimal import Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import select

from app.models import (FinishedBatch, FinishedOutbound, FinishedReceipt,
                        InventoryTransaction, MaterialAdjustment, MaterialBatch, MaterialCount, MaterialIssueRecord,
                        MaterialMovement, MaterialTransfer, OperationLog, ProductionOrder,
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
    ledger_by_document = defaultdict(list)
    ledger_by_id = {row.id: row for row in ledger}
    actual = Counter()
    for row in ledger:
        ledger_by_document[row.document_id].append(row)
        scope = 'material' if row.material_batch_id is not None else 'finished'
        batch_id = row.material_batch_id if scope == 'material' else row.finished_batch_id
        chains[scope, batch_id].append(row)
        actual[row.document_id, scope, batch_id, row.kind, row.change_quantity] += 1
        doc = documents.get(row.document_id)
        if not doc or (doc.kind, doc.operator_id) != (row.kind, row.operator_id):
            problem('ledger', row.id, 'document kind/operator mismatch')

    reversals_by_original = defaultdict(list)
    for doc in documents.values():
        if doc.kind != 'reversal':
            if any((doc.reverses_document_id, doc.reverses_kind,
                    doc.original_document_no_snapshot, doc.reversal_reason)):
                problem('reversal', doc.id, 'ordinary document has reversal fields')
            continue
        original = documents.get(doc.reverses_document_id)
        if (original is None or original.kind not in ('material_outbound', 'finished_outbound')
                or doc.reverses_kind != original.kind
                or doc.original_document_no_snapshot != original.document_no
                or not doc.reversal_reason or not doc.reversal_reason.strip()):
            problem('reversal', doc.id, 'missing, unsupported or inconsistent original document')
            continue
        reversals_by_original[original.id].append(doc)
        source_rows = ledger_by_document[original.id]
        reversed_rows = ledger_by_document[doc.id]
        if len(source_rows) != 1 or len(reversed_rows) != 1:
            problem('reversal', doc.id, 'outbound reversal must pair exactly one ledger row')
            continue
        source, reverse = source_rows[0], reversed_rows[0]
        if (reverse.reverses_transaction_id != source.id or reverse.kind != 'reversal'
                or reverse.material_batch_id != source.material_batch_id
                or reverse.finished_batch_id != source.finished_batch_id
                or reverse.unit != source.unit or reverse.change_quantity != -source.change_quantity
                or reverse.source_location_id is not None
                or reverse.target_location_id != source.source_location_id):
            problem('reversal', doc.id, 'reverse ledger does not invert its original row')
    for original_id, rows in reversals_by_original.items():
        if len(rows) != 1:
            problem('reversal', original_id, 'original document reversed more than once')
    for row in ledger:
        if row.kind == 'reversal':
            source = ledger_by_id.get(row.reverses_transaction_id)
            doc = documents.get(row.document_id)
            if (source is None or doc is None or doc.kind != 'reversal'
                    or source.document_id != doc.reverses_document_id):
                problem('reversal', row.id, 'orphan or incorrectly linked reverse ledger')
        elif row.reverses_transaction_id is not None:
            problem('reversal', row.id, 'ordinary ledger has reverse link')

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
                if row.kind == 'material_receipt' or row.kind in ('putaway', 'transfer', 'count_gain', 'adjustment') and row.change_quantity > 0:
                    received += row.change_quantity
            if batch.remaining_quantity < 0 or balance != batch.remaining_quantity:
                problem(scope, batch.id, 'stock differs from ledger sum or is negative')
            if scope == 'material' and received != batch.received_quantity:
                problem(scope, batch.id, 'cumulative inbound quantity differs from ledger')
            if scope == 'material' and ((batch.stock_status == 'pending') != (batch.location_id is None)):
                problem(scope, batch.id, 'pending status/location mismatch')
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
    putaway_totals = defaultdict(lambda: Decimal('0'))
    for row in session.scalars(select(MaterialTransfer)):
        doc = documents.get(row.document_id)
        if doc is None or doc.kind not in ('putaway', 'transfer'):
            problem('transfer', row.id, 'missing or wrong document')
            continue
        expected[row.document_id, 'material', row.source_batch_id, doc.kind, -row.quantity] += 1
        expected[row.document_id, 'material', row.target_batch_id, doc.kind, row.quantity] += 1
        if row.receipt_movement_id is not None:
            if doc.kind != 'putaway':
                problem('transfer', row.id, 'receipt linkage on non-putaway')
            putaway_totals[row.receipt_movement_id] += row.quantity
    for row in session.scalars(select(MaterialMovement)):
        doc = documents.get(row.document_id)
        if doc and doc.kind == 'material_receipt':
            if row.shelved_quantity is None:
                problem('receipt', row.id, 'missing shelved quantity')
            else:
                batch = session.get(MaterialBatch, row.batch_id)
                if batch.stock_status == 'pending':
                    if row.shelved_quantity != putaway_totals[row.id] or batch.remaining_quantity != row.quantity - row.shelved_quantity:
                        problem('receipt', row.id, 'pending/shelved quantities differ from putaway history')
                elif row.shelved_quantity != row.quantity:
                    problem('receipt', row.id, 'legacy shelved receipt has pending quantity')
    for row in session.scalars(select(MaterialCount)):
        doc = documents.get(row.document_id)
        kind = 'count_gain' if row.difference > 0 else 'count_loss' if row.difference < 0 else 'count_match'
        if doc is None or doc.kind != kind:
            problem('count', row.id, 'missing or wrong document')
        elif row.difference:
            expected[row.document_id, 'material', row.batch_id, kind, row.difference] += 1
    for row in session.scalars(select(MaterialAdjustment)):
        doc = documents.get(row.document_id)
        if doc is None or doc.kind != 'adjustment' or row.after_quantity != row.before_quantity + row.change_quantity:
            problem('adjustment', row.id, 'missing or inconsistent adjustment document')
        else:
            expected[row.document_id, 'material', row.batch_id, 'adjustment', row.change_quantity] += 1
    for original_id, rows in reversals_by_original.items():
        for reversal in rows:
            for source in ledger_by_document[original_id]:
                scope = 'material' if source.material_batch_id is not None else 'finished'
                batch_id = source.material_batch_id if scope == 'material' else source.finished_batch_id
                expected[reversal.id, scope, batch_id, 'reversal', -source.change_quantity] += 1
    for key in expected.keys() | actual.keys():
        if expected[key] != actual[key]:
            problem('document', key[0], 'details and ledger differ')
    logged = {int(row.entity_id) for row in session.scalars(select(OperationLog).where(
        OperationLog.entity_type == 'stock_documents')) if row.entity_id and row.entity_id.isdigit()}
    posted = {key[0] for key in actual}
    for identity in documents:
        if (identity not in posted and documents[identity].kind != 'count_match') or identity not in logged:
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
