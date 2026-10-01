"""Shared evidence checks for live reversal and historical reconciliation.

No quantity provenance allocation is inferred for merged batches.
"""
from collections import defaultdict

SUPPORTED_KINDS = frozenset(('material_outbound', 'finished_outbound', 'material_receipt',
                             'putaway', 'transfer', 'finished_receipt', 'adjustment', 'issue', 'supplement', 'return'))
MOVES = frozenset(('putaway', 'transfer'))


def complete_inverse(original, reversal, by_document):
    if (not original or not reversal or original.kind not in SUPPORTED_KINDS
            or reversal.kind != 'reversal' or reversal.reverses_document_id != original.id
            or reversal.reverses_kind != original.kind
            or reversal.original_document_no_snapshot != original.document_no
            or not reversal.reversal_reason or not reversal.reversal_reason.strip()
            or reversal.id <= original.id or reversal.created_at < original.created_at):
        return False
    sources = by_document.get(original.id, [])
    rows = by_document.get(reversal.id, [])
    expected_count = (len(sources) if original.kind in ('issue', 'supplement', 'return', 'finished_outbound')
                      else 2 if original.kind in MOVES else 1)
    if expected_count < 1:
        return False
    if len(sources) != expected_count or len(rows) != expected_count:
        return False
    source_map = {row.id: row for row in sources}
    if len({row.reverses_transaction_id for row in rows}) != expected_count:
        return False
    for row in rows:
        source = source_map.get(row.reverses_transaction_id)
        if (source is None or source.kind != original.kind or row.kind != 'reversal'
                or source.operator_id != original.operator_id
                or row.operator_id != reversal.operator_id
                or row.id <= source.id or row.created_at < source.created_at
                or row.material_batch_id != source.material_batch_id
                or row.finished_batch_id != source.finished_batch_id
                or row.unit != source.unit or row.change_quantity != -source.change_quantity
                or row.source_location_id != source.target_location_id
                or row.target_location_id != source.source_location_id):
            return False
    return True


def downstream_blockers(original, source_id, target_id, documents, ledger, counts, *, before_id=None):
    """Only explicit effective later facts block; a historical audit uses its posting cutoff.

Document IDs order same-material writes because all writers hold the material lock
before inserting documents. Cross-material ordering is never used as provenance.
"""
    docs = {key: doc for key, doc in documents.items() if before_id is None or key < before_id}
    by_doc = defaultdict(list)
    for row in ledger:
        if row.document_id in docs:
            by_doc[row.document_id].append(row)
    neutral = set()
    for doc in docs.values():
        if doc.kind == 'reversal':
            parent = docs.get(doc.reverses_document_id)
            # Both facts must be later than the candidate; reversing an older inflow
            # is a new effective debit, not a neutral pair after this boundary.
            if parent and parent.id > original.id and complete_inverse(parent, doc, by_doc):
                neutral.update((parent.id, doc.id))
    blocked = set()
    relevant = {source_id, target_id}
    for count in counts:
        if count.batch_id in relevant and count.document_id in docs and count.document_id > original.id:
            blocked.add(count.document_id)  # Includes zero-difference counts without ledger rows.
    if original.kind in MOVES:
        for row in ledger:
            doc = docs.get(row.document_id)
            if (doc is None or doc.id <= original.id or doc.id in neutral
                    or row.material_batch_id != target_id):
                continue
            if doc.kind in ('issue', 'supplement', 'return', 'adjustment') or row.change_quantity < 0:
                blocked.add(doc.id)
    return [docs[key] for key in sorted(blocked)]


def load_evidence(session, batch_ids):
    from sqlalchemy import select
    from app.models import InventoryTransaction, MaterialCount, StockDocument
    counts = list(session.scalars(select(MaterialCount).where(MaterialCount.batch_id.in_(batch_ids))))
    ids = set(session.scalars(select(InventoryTransaction.document_id).where(
        InventoryTransaction.material_batch_id.in_(batch_ids))))
    ids.update(row.document_id for row in counts)
    if not ids:
        return {}, [], counts
    documents = {row.id: row for row in session.scalars(select(StockDocument).where(StockDocument.id.in_(ids)))}
    ids.update(row.reverses_document_id for row in documents.values() if row.reverses_document_id)
    for row in session.scalars(select(StockDocument).where(
            StockDocument.id.in_(ids) | StockDocument.reverses_document_id.in_(ids))):
        documents[row.id] = row
    ledger = list(session.scalars(select(InventoryTransaction).where(
        InventoryTransaction.document_id.in_(documents)).order_by(InventoryTransaction.id)))
    return documents, ledger, counts


def reversed_document_ids(session, identities):
    """Only proven complete inverse facts neutralize a posting; no existence-only filter."""
    from sqlalchemy import select
    from app.models import StockDocument, InventoryTransaction, MaterialIssueRecord, FinishedOutbound
    identities = set(identities)
    if not identities:
        return set()
    originals = {d.id: d for d in session.scalars(select(StockDocument).where(StockDocument.id.in_(identities)))}
    inverses = list(session.scalars(select(StockDocument).where(StockDocument.reverses_document_id.in_(identities))))
    by_doc = defaultdict(list)
    for row in session.scalars(select(InventoryTransaction).where(
            InventoryTransaction.document_id.in_(identities | {d.id for d in inverses}))):
        by_doc[row.document_id].append(row)
    # Multi-row originals must have their entire fact detail represented in ledger.
    # Otherwise a truncated original + matching partial inverse could falsely neutralize it.
    complete_facts = set(identities)
    for kind, model, batch_field, sign in [('issue', MaterialIssueRecord, 'material_batch_id', -1),
                                         ('supplement', MaterialIssueRecord, 'material_batch_id', -1),
                                         ('return', MaterialIssueRecord, 'material_batch_id', 1),
                                         ('finished_outbound', FinishedOutbound, 'finished_batch_id', -1)]:
        selected = {key for key, d in originals.items() if d.kind == kind}
        details = defaultdict(list)
        for row in session.scalars(select(model).where(model.document_id.in_(selected))):
            details[row.document_id].append(row)
        for key in selected:
            expected = sorted((r.batch_id, r.quantity * sign) for r in details[key])
            actual = sorted((getattr(r, batch_field), r.change_quantity) for r in by_doc[key])
            if not expected or expected != actual:
                complete_facts.discard(key)
    return {d.reverses_document_id for d in inverses if d.reverses_document_id in complete_facts
            and complete_inverse(originals.get(d.reverses_document_id), d, by_doc)}


def effective_material_records(session, order_material_ids):
    from sqlalchemy import select
    from app.models import MaterialIssueRecord
    rows = list(session.scalars(select(MaterialIssueRecord).where(
        MaterialIssueRecord.order_material_id.in_(order_material_ids)).order_by(MaterialIssueRecord.id)))
    reversed_ids = reversed_document_ids(session, [r.document_id for r in rows])
    return [r for r in rows if r.document_id not in reversed_ids]
