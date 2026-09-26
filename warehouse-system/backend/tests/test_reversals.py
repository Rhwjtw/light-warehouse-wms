"""First-batch reversals against the guarded isolated MySQL target."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app import reversal_service
from app.database import Base, SessionLocal
from app.models import (FinishedBatch, InventoryTransaction, MaterialBatch,
                        OperationLog, StockDocument, User)
from app.reconciliation import reconcile
from app.schemas import StockReversalInput
from test_phase3 import inventory, out, receive, shelf
from test_phase2 import context  # noqa: F401 - shared isolated-MySQL savepoint fixture
from test_phase4 import balance, finished_inventory, post
from test_phase7_concurrency import committed_production

pytestmark = pytest.mark.mysql


def assert_production_fact_grants(connection):
    """Run against real MySQL privileges, not a mocked permission decision."""
    grants = [row[0] for row in connection.exec_driver_sql('SHOW GRANTS')]
    assert any(grant.startswith('GRANT USAGE ON *.* TO ') for grant in grants)
    assert any(grant.startswith('GRANT SELECT ON `warehouse_system`.* TO ') for grant in grants)
    assert len(grants) == len(Base.metadata.tables) + 2
    def table_grant(table):
        matches = [grant for grant in grants if f'.`{table}`' in grant]
        assert len(matches) == 1, (table, matches)
        return matches[0]
    for table in ('stock_documents', 'inventory_transactions', 'operation_logs',
                  'material_issue_records', 'material_transfers', 'material_counts',
                  'material_adjustments', 'finished_receipts', 'finished_outbounds'):
        assert 'GRANT INSERT ON' in table_grant(table)
    for table, fields in {
        'material_movements': ('shelved_quantity',),
        'material_batches': ('received_quantity', 'remaining_quantity', 'updated_at'),
        'finished_batches': ('remaining_quantity', 'updated_at'),
    }.items():
        grant = table_grant(table)
        assert 'DELETE' not in grant and 'UPDATE (' in grant
        actual_fields = grant.partition('UPDATE (')[2].partition(') ON')[0]
        assert actual_fields == ', '.join(f'`{field}`' for field in fields)
    limited = {'stock_documents', 'inventory_transactions', 'operation_logs',
               'material_issue_records', 'material_transfers', 'material_counts',
               'material_adjustments', 'finished_receipts', 'finished_outbounds',
               'material_movements', 'material_batches', 'finished_batches'}
    for table in Base.metadata.tables:
        if table not in limited:
            assert 'GRANT INSERT, UPDATE, DELETE ON' in table_grant(table)


def reverse(client, headers, identity, *, role='warehouse', request_id=None, reason='原出库录入错误'):
    return client.post(f'/api/stock-documents/{identity}/reverse', headers=headers[role],
                       json={'request_id': request_id or uuid4().hex, 'reason': reason})


def test_material_outbound_reversal(inventory):
    client, headers, connection, payload = inventory
    assert_production_fact_grants(connection)
    batch_id = shelf(inventory, receive(inventory)).json()['data']['target_batch_id']
    original = out(inventory, batch_id)
    assert original.status_code == 200, original.text
    identity = original.json()['data']['id']
    # 反向流水的前后量必须使用冲销当时的实时余额，而非旧流水。
    assert out(inventory, batch_id, quantity='1').status_code == 200
    preview = client.get(f'/api/stock-documents/{identity}/reversal-preview', headers=headers['warehouse']).json()['data']
    assert preview['eligible'] and Decimal(preview['changes'][0]['before_quantity']) == 7
    assert reverse(client, headers, identity, role='workshop').status_code == 403
    assert reverse(client, headers, identity, reason='  ').status_code == 422
    key = uuid4().hex
    response = reverse(client, headers, identity, request_id=key)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['kind'] == 'reversal' and data['reverses_document_id'] == identity
    assert data['original_document_no'] == original.json()['data']['document_no']
    assert reverse(client, headers, identity, request_id=key).json()['data']['id'] == data['id']
    duplicate = reverse(client, headers, identity)
    assert duplicate.status_code == 409 and duplicate.json()['code'] == 'ALREADY_REVERSED'
    assert reverse(client, headers, data['id']).status_code == 409
    original_detail = client.get(f'/api/stock-documents/{identity}', headers=headers['accountant']).json()['data']
    assert original_detail['status'] == 'reversed' and original_detail['reversal']['id'] == data['id']
    assert client.get('/api/stock-documents/reversals', headers=headers['accountant']).json()['data']['total'] == 1
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        row = session.scalar(select(InventoryTransaction).where(InventoryTransaction.document_id == data['id']))
        source = session.get(InventoryTransaction, row.reverses_transaction_id)
        assert (row.before_quantity, row.change_quantity, row.after_quantity) == (Decimal(7), Decimal(2), Decimal(9))
        assert row.change_quantity == -source.change_quantity
        assert session.get(MaterialBatch, batch_id).remaining_quantity == Decimal(9)
        assert session.scalar(select(func.count()).select_from(OperationLog).where(
            OperationLog.entity_type == 'stock_documents', OperationLog.entity_id == str(data['id']))) == 1
        assert reconcile(session)['ok'], reconcile(session)['problems']


def test_finished_outbound_reversal(finished_inventory):
    client, headers, connection, payload = finished_inventory
    assert_production_fact_grants(connection)
    assert post(finished_inventory).status_code == 200
    original = post(finished_inventory, 'finished-outbounds', quantity='200')
    assert original.status_code == 200, original.text
    identity = original.json()['data']['id']
    assert post(finished_inventory, 'finished-outbounds', quantity='50').status_code == 200
    response = reverse(client, headers, identity)
    assert response.status_code == 200, response.text
    row = response.json()['data']['transactions'][0]
    assert (Decimal(row['before_quantity']), Decimal(row['change_quantity']), Decimal(row['after_quantity'])) == (750, 200, 950)
    assert Decimal(balance(finished_inventory)['quantity']) == 950
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        assert reconcile(session)['ok'], reconcile(session)['problems']


def test_real_unique_reversal_collision_maps_to_409(inventory):
    client, headers, connection, _ = inventory
    batch_id = shelf(inventory, receive(inventory)).json()['data']['target_batch_id']
    identity = out(inventory, batch_id).json()['data']['id']
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        before = session.scalar(select(func.count()).select_from(StockDocument))

    def competing_insert(mapper, db_connection, target):
        if target.kind != 'reversal':
            return
        # 真正触发 MySQL 唯一键；竞争行与本次插入都留在同一个 savepoint 内回滚。
        db_connection.exec_driver_sql(
            'INSERT INTO stock_documents (document_no, request_id, kind, reverses_document_id, '
            'reverses_kind, original_document_no_snapshot, reversal_reason, operator_id, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())',
            ('RV-RACE-' + uuid4().hex, uuid4().hex, 'reversal', target.reverses_document_id,
             target.reverses_kind, target.original_document_no_snapshot, '隔离唯一键竞争', target.operator_id))

    event.listen(StockDocument, 'before_insert', competing_insert)
    try:
        response = reverse(client, headers, identity)
    finally:
        event.remove(StockDocument, 'before_insert', competing_insert)
    assert response.status_code == 409 and response.json()['code'] == 'ALREADY_REVERSED'
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        assert session.scalar(select(func.count()).select_from(StockDocument)) == before
        assert session.get(MaterialBatch, batch_id).remaining_quantity == Decimal(8)
        assert reconcile(session)['ok'], reconcile(session)['problems']


@pytest.mark.parametrize('failure', ['document', 'stock', 'ledger', 'audit'])
def test_reversal_rollback(inventory, failure):
    client, headers, connection, payload = inventory
    batch_id = shelf(inventory, receive(inventory)).json()['data']['target_batch_id']
    identity = out(inventory, batch_id).json()['data']['id']
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        before = tuple(session.scalar(select(func.count()).select_from(model)) for model in
                       (StockDocument, InventoryTransaction, OperationLog))
    if failure == 'document':
        model, event_name = StockDocument, 'after_insert'
        def fail_document(mapper, connection, target):
            if target.kind == 'reversal':
                raise RuntimeError('injected reversal document insert failure')
        listener = fail_document
    elif failure == 'stock':
        model, event_name = MaterialBatch, 'after_update'
        def fail_stock(mapper, connection, target):
            if target.id == batch_id:
                raise RuntimeError('injected reversal stock update failure')
        listener = fail_stock
    elif failure == 'ledger':
        model, event_name = InventoryTransaction, 'before_insert'
        def fail_ledger(mapper, connection, target):
            if target.kind == 'reversal':
                raise RuntimeError('injected reverse ledger insert failure')
        listener = fail_ledger
    else:
        model, event_name = OperationLog, 'after_insert'
        def fail_audit(mapper, connection, target):
            if target.action == 'reverse_stock_document':
                raise RuntimeError('injected reversal audit insert failure')
        listener = fail_audit
    event.listen(model, event_name, listener)
    try:
        response = reverse(client, headers, identity)
        assert response.status_code == 500 and response.json()['code'] == 'INTERNAL_ERROR'
        assert 'SQL' not in response.json()['message']
    finally:
        event.remove(model, event_name, listener)
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        after = tuple(session.scalar(select(func.count()).select_from(model)) for model in
                      (StockDocument, InventoryTransaction, OperationLog))
        assert after == before
        assert session.get(MaterialBatch, batch_id).remaining_quantity == Decimal(8)
        assert reconcile(session)['ok'], reconcile(session)['problems']


def test_concurrent_reversal_two_connections(committed_production):
    """Two simultaneously held MySQL connections race through the real service and commit."""
    client, headers, _, prefix = committed_production
    def create(resource, **fields):
        payload = {'code': prefix + resource[:2], 'name': prefix + resource[:2], **fields}
        if resource == 'locations': payload.pop('code')
        result = client.post('/api/' + resource, headers=headers['admin'], json=payload)
        assert result.status_code == 200, result.text
        return result.json()['data']['id']
    material = create('materials', unit='kg')
    supplier = create('suppliers')
    location = create('locations')
    from app.stock_service import business_today
    from datetime import timedelta
    today = business_today()
    receipt = client.post('/api/material-receipts', headers=headers['warehouse'], json={
        'request_id': uuid4().hex, 'material_id': material, 'supplier_id': supplier,
        'batch_no': prefix, 'quantity': '10', 'unit': 'kg',
        'production_date': str(today - timedelta(days=1)), 'expiry_date': str(today + timedelta(days=10))})
    assert receipt.status_code == 200, receipt.text
    detail = client.get(f"/api/material-receipts/{receipt.json()['data']['id']}", headers=headers['warehouse']).json()['data']
    putaway = client.post('/api/material-putaways', headers=headers['warehouse'], json={
        'request_id': uuid4().hex, 'receipt_movement_id': detail['receipt_movement_id'],
        'location_id': location, 'quantity': '10'})
    assert putaway.status_code == 200, putaway.text
    batch_id = putaway.json()['data']['target_batch_id']
    outbound = client.post('/api/material-outbounds', headers=headers['warehouse'], json={
        'request_id': uuid4().hex, 'material_id': material, 'batch_id': batch_id, 'quantity': '2', 'unit': 'kg'})
    assert outbound.status_code == 200, outbound.text
    identity = outbound.json()['data']['id']
    barrier = Barrier(2)
    def task(_):
        connection_id = None
        try:
            with SessionLocal.begin() as session:
                connection_id = session.connection().exec_driver_sql('SELECT CONNECTION_ID()').scalar_one()
                assert_production_fact_grants(session.connection())
                user = session.scalar(select(User).where(User.username == prefix))
                barrier.wait(timeout=10)
                reversal_service.reverse_document(session, user, identity,
                    StockReversalInput(request_id=uuid4().hex, reason='并发错误出库冲销'))
            return connection_id, 200, None
        except HTTPException as exc:
            return connection_id, exc.status_code, getattr(exc, 'code', None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(task, range(2)))
    assert len({connection_id for connection_id, _, _ in responses}) == 2
    assert sorted(status for _, status, _ in responses) == [200, 409]
    assert [code for _, status, code in responses if status == 409] == ['ALREADY_REVERSED']
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(StockDocument).where(
            StockDocument.reverses_document_id == identity)) == 1
        original_tx_id = session.scalar(select(InventoryTransaction.id).where(
            InventoryTransaction.document_id == identity))
        assert session.scalar(select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.reverses_transaction_id == original_tx_id)) == 1
        assert session.get(MaterialBatch, batch_id).remaining_quantity == Decimal(10)
        assert reconcile(session)['ok'], reconcile(session)['problems']
