"""Receipt staging and physical location inventory regression."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import InventoryTransaction, MaterialBatch, MaterialMovement, MaterialTransfer
from app import material_location_service
from app.reconciliation import reconcile
from app.stock_service import business_today
from test_phase6 import context, scenario  # noqa: F401


def post(client, headers, path, payload, role='warehouse'):
    return client.post('/api/' + path, headers=headers[role], json={'request_id': uuid4().hex, **payload})


def available(client, headers, material):
    result = client.get('/api/material-stock', headers=headers['warehouse'], params={'q': material['code']})
    assert result.status_code == 200, result.text
    return Decimal(next(row['remaining_quantity'] for row in result.json()['data']['items'] if row['id'] == material['id']))


def test_receipt_putaway_transfer_issue_return_consistency(context):
    data = scenario(context, 'locations', stock_batches=())
    client, headers, connection, _ = context
    material = data['materials'][0]
    location_a = data['location']
    location_b = client.post('/api/locations', headers=headers['warehouse'], json={'name': 'B区2号架'}).json()['data']
    assert 'code' not in location_b and location_b['name'] == 'B区2号架'
    assert client.get('/api/locations', headers=headers['accountant'], params={'q': 'B区2号架'}).json()['data']['total'] == 1
    assert client.put(f"/api/locations/{location_b['id']}", headers=headers['warehouse'],
                      json={'name': 'B区3号架', 'notes': '测试移库'}).status_code == 200
    receipt = post(client, headers, 'material-receipts', {
        'material_id': material['id'], 'batch_no': 'GLY-001', 'quantity': '100', 'unit': 'kg',
        'production_date': str(business_today() - timedelta(days=10)),
        'expiry_date': str(business_today() + timedelta(days=30)), 'supplier_id': data['supplier']['id'],
    })
    assert receipt.status_code == 200, receipt.text
    pending_id = receipt.json()['data']['batch_id']
    receipt_id = receipt.json()['data']['id']
    summary = client.get(f'/api/material-receipts/{receipt_id}', headers=headers['warehouse']).json()['data']
    movement_id = summary['receipt_movement_id']
    assert (summary['status'], summary['pending_quantity'], summary['shelved_quantity']) == ('pending', '100.000000', '0.000000')
    assert available(client, headers, material) == 0
    assert client.get('/api/material-fefo', headers=headers['warehouse'], params={'material_id': material['id']}).json()['data']['total'] == 0
    assert client.get('/api/material-location-stock', headers=headers['warehouse']).json()['data']['total'] == 0
    assert post(client, headers, 'material-outbounds', {'material_id': material['id'], 'batch_id': pending_id,
                                                        'quantity': '1', 'unit': 'kg'}).status_code == 422
    order_id = data['order']['id']
    order_material_id = data['order']['materials'][0]['id']
    assert post(client, headers, f'production-orders/{order_id}/issue', {
        'order_material_id': order_material_id, 'unit': 'kg',
        'allocations': [{'batch_id': pending_id, 'quantity': '1'}]}, role='workshop').status_code == 422
    inactive = client.post('/api/locations', headers=headers['warehouse'],
                           json={'name': '停用上架目标', 'is_active': False}).json()['data']
    assert post(client, headers, 'material-putaways', {'receipt_movement_id': movement_id,
        'location_id': inactive['id'], 'quantity': '1'}).status_code == 422
    assert post(client, headers, 'material-putaways', {'receipt_movement_id': movement_id,
        'location_id': location_a['id'], 'quantity': '0'}).status_code == 422

    first = post(client, headers, 'material-putaways', {'receipt_movement_id': movement_id,
                                                        'location_id': location_a['id'], 'quantity': '60'})
    assert first.status_code == 200, first.text
    replay_payload = {'request_id': first.json()['data']['request_id'], 'receipt_movement_id': movement_id,
                      'location_id': location_a['id'], 'quantity': '60', 'notes': None}
    assert client.post('/api/material-putaways', headers=headers['warehouse'], json=replay_payload).json()['data'] == first.json()['data']
    assert client.post('/api/material-putaways', headers=headers['warehouse'],
                       json={**replay_payload, 'quantity': '1'}).status_code == 409
    a_batch_id = first.json()['data']['target_batch_id']
    assert available(client, headers, material) == 60
    summary = client.get(f'/api/material-receipts/{receipt_id}', headers=headers['warehouse']).json()['data']
    assert (summary['status'], Decimal(summary['pending_quantity'])) == ('partial', 40)
    assert post(client, headers, 'material-putaways', {'receipt_movement_id': movement_id,
        'location_id': location_a['id'], 'quantity': '40.000001'}).status_code == 409
    second = post(client, headers, 'material-putaways', {'receipt_movement_id': movement_id,
                                                         'location_id': location_b['id'], 'quantity': '40'})
    assert second.status_code == 200, second.text
    b_batch_id = second.json()['data']['target_batch_id']
    assert available(client, headers, material) == 100
    summary = client.get(f'/api/material-receipts/{receipt_id}', headers=headers['warehouse']).json()['data']
    assert (summary['status'], Decimal(summary['pending_quantity'])) == ('shelved', 0)
    assert len(summary['allocations']) == 2
    assert post(client, headers, 'material-putaways', {'receipt_movement_id': movement_id,
        'location_id': location_b['id'], 'quantity': '1'}).status_code == 409

    conflicting = post(client, headers, 'material-receipts', {
        'material_id': material['id'], 'batch_no': 'GLY-001', 'quantity': '1', 'unit': 'kg',
        'production_date': str(business_today() - timedelta(days=10)),
        'expiry_date': str(business_today() + timedelta(days=31)), 'supplier_id': data['supplier']['id'],
    })
    conflicting_movement = client.get(f"/api/material-receipts/{conflicting.json()['data']['id']}",
                                      headers=headers['warehouse']).json()['data']['receipt_movement_id']
    assert post(client, headers, 'material-putaways', {'receipt_movement_id': conflicting_movement,
        'location_id': location_b['id'], 'quantity': '1'}).status_code == 409
    assert available(client, headers, material) == 100

    move = post(client, headers, 'material-transfers', {'source_batch_id': a_batch_id,
              'target_location_id': location_b['id'], 'quantity': '30'})
    assert move.status_code == 200, move.text
    assert move.json()['data']['target_batch_id'] == b_batch_id
    assert available(client, headers, material) == 100
    assert post(client, headers, 'material-transfers', {'source_batch_id': a_batch_id,
        'target_location_id': location_b['id'], 'quantity': '31'}).status_code == 409
    assert post(client, headers, 'material-transfers', {'source_batch_id': a_batch_id,
        'target_location_id': location_a['id'], 'quantity': '1'}).status_code == 422
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        rows = session.scalars(select(InventoryTransaction).where(
            InventoryTransaction.document_id == move.json()['data']['id'])).all()
        assert len(rows) == 2 and sum(row.change_quantity for row in rows) == 0
        assert {row.source_location_id for row in rows} == {location_a['id']}
        assert {row.target_location_id for row in rows} == {location_b['id']}
        assert session.scalar(select(func.sum(MaterialBatch.remaining_quantity)).where(
            MaterialBatch.material_id == material['id'], MaterialBatch.stock_status == 'shelved')) == available(client, headers, material)

    suggestion = client.get(f'/api/production-orders/{order_id}/materials/{material["id"]}/fefo-suggestion',
                            headers=headers['workshop'], params={'quantity': '20'}).json()['data']
    assert suggestion['batches'][0]['location_name'] == location_a['name']
    issue = post(client, headers, f'production-orders/{order_id}/issue', {
        'order_material_id': order_material_id, 'unit': 'kg',
        'allocations': [{'batch_id': a_batch_id, 'quantity': '20'}]}, role='workshop')
    assert issue.status_code == 200, issue.text
    assert available(client, headers, material) == 80
    returned = post(client, headers, f'production-orders/{order_id}/return', {
        'order_material_id': order_material_id, 'unit': 'kg',
        'allocations': [{'batch_id': a_batch_id, 'quantity': '5'}]}, role='workshop')
    assert returned.status_code == 200, returned.text
    assert available(client, headers, material) == 85
    stock_page = client.get('/api/material-stock', headers=headers['warehouse'],
                            params={'page_size': 100}).json()['data']
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        shelved_total = session.scalar(select(func.sum(MaterialBatch.remaining_quantity)).where(
            MaterialBatch.stock_status == 'shelved')) or Decimal('0')
        pending_total = session.scalar(select(func.sum(MaterialBatch.remaining_quantity)).where(
            MaterialBatch.stock_status == 'pending')) or Decimal('0')
        assert sum(Decimal(row['remaining_quantity']) for row in stock_page['items']) == shelved_total == 85
        assert pending_total == 1
        assert reconcile(session)['ok']
    assert client.get('/api/material-location-stock', headers=headers['warehouse'],
                      params={'location_id': location_a['id']}).json()['data']['total'] == 1
    assert client.get('/api/material-location-stock', headers=headers['warehouse'],
                      params={'material_id': material['id'], 'batch_no': 'GLY-001'}).json()['data']['total'] == 2
    assert client.get(f'/api/material-location-stock/{a_batch_id}', headers=headers['warehouse']).json()['data']['transactions']['total'] >= 4
    assert client.get('/api/material-transactions', headers=headers['warehouse'],
                      params={'kind': 'transfer'}).json()['data']['total'] == 2
    assert client.get('/api/material-transfers', headers=headers['warehouse'],
                      params={'kind': 'transfer'}).json()['data']['total'] == 1


def test_count_permissions_and_disabled_location(context):
    data = scenario(context, 'count', stock_batches=())
    client, headers, _, _ = context
    material = data['materials'][0]
    receipt = post(client, headers, 'material-receipts', {
        'material_id': material['id'], 'batch_no': 'COUNT-001', 'quantity': '10', 'unit': 'kg',
        'production_date': str(business_today()), 'expiry_date': str(business_today() + timedelta(days=30)),
        'supplier_id': data['supplier']['id']})
    movement_id = client.get(f"/api/material-receipts/{receipt.json()['data']['id']}",
                             headers=headers['warehouse']).json()['data']['receipt_movement_id']
    source = {'receipt_movement_id': movement_id, 'location_id': data['location']['id'], 'quantity': '10'}
    assert post(client, headers, 'material-putaways', source, role='accountant').status_code == 403
    assert post(client, headers, 'material-putaways', source, role='workshop').status_code == 403
    target = post(client, headers, 'material-putaways', source).json()['data']['target_batch_id']
    assert post(client, headers, 'material-counts', {'batch_id': target,
        'book_quantity': '10', 'actual_quantity': '12', 'reason': '复盘盘盈'}, role='accountant').status_code == 403
    gain = post(client, headers, 'material-counts', {'batch_id': target,
        'book_quantity': '10', 'actual_quantity': '12', 'reason': '复盘盘盈'})
    assert gain.status_code == 200, gain.text
    assert available(client, headers, material) == 12
    assert client.post('/api/material-counts', headers=headers['warehouse'], json={
        'request_id': gain.json()['data']['request_id'], 'batch_id': target,
        'book_quantity': '10', 'actual_quantity': '12', 'reason': '复盘盘盈'}).json()['data'] == gain.json()['data']
    loss = post(client, headers, 'material-counts', {'batch_id': target,
        'book_quantity': '12', 'actual_quantity': '9', 'reason': '复盘盘亏'})
    assert loss.status_code == 200, loss.text
    assert available(client, headers, material) == 9
    match = post(client, headers, 'material-counts', {'batch_id': target,
        'book_quantity': '9', 'actual_quantity': '9', 'reason': '账实一致'})
    assert match.status_code == 200 and match.json()['data']['kind'] == 'count_match'
    assert post(client, headers, 'material-counts', {'batch_id': target,
        'book_quantity': '12', 'actual_quantity': '8'}).status_code == 409
    assert client.get('/api/material-counts', headers=headers['accountant']).json()['data']['total'] == 3
    change = {'batch_id': target, 'book_quantity': '9', 'change_quantity': '2', 'reason': '有据调整'}
    assert post(client, headers, 'material-adjustments', change, role='accountant').status_code == 403
    adjusted = post(client, headers, 'material-adjustments', change)
    assert adjusted.status_code == 200, adjusted.text
    assert available(client, headers, material) == 11
    assert client.get('/api/material-transactions', headers=headers['warehouse'],
                      params={'kind': 'adjustment'}).json()['data']['total'] == 1
    assert post(client, headers, 'material-adjustments', {**change, 'book_quantity': '11',
        'change_quantity': '-12'}).status_code == 422
    assert post(client, headers, 'material-adjustments', {**change, 'book_quantity': '9'}).status_code == 409
    disabled = client.post('/api/locations', headers=headers['warehouse'], json={'name': '禁用库位', 'is_active': False}).json()['data']
    assert post(client, headers, 'material-transfers', {'source_batch_id': target,
        'target_location_id': disabled['id'], 'quantity': '1'}).status_code == 422
    assert client.delete(f"/api/locations/{data['location']['id']}", headers=headers['warehouse']).status_code == 409
    assert client.get('/api/material-location-stock', headers=headers['accountant']).status_code == 200
    assert client.get('/api/material-location-stock').status_code == 401


def test_location_postings_rollback_together(context, monkeypatch):
    data = scenario(context, 'locrollback', stock_batches=())
    client, headers, connection, _ = context
    material = data['materials'][0]
    receipt = post(client, headers, 'material-receipts', {
        'material_id': material['id'], 'batch_no': 'ROLL-001', 'quantity': '10', 'unit': 'kg',
        'production_date': str(business_today()), 'expiry_date': str(business_today() + timedelta(days=30)),
        'supplier_id': data['supplier']['id']})
    movement_id = client.get(f"/api/material-receipts/{receipt.json()['data']['id']}",
                             headers=headers['warehouse']).json()['data']['receipt_movement_id']
    original = material_location_service._ledger
    calls = 0
    def fail_second_ledger(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('injected second ledger failure')
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(material_location_service, '_ledger', fail_second_ledger)
        result = post(client, headers, 'material-putaways', {
            'receipt_movement_id': movement_id, 'location_id': data['location']['id'], 'quantity': '10'})
    assert result.status_code == 500
    assert available(client, headers, material) == 0
    pending = client.get(f"/api/material-receipts/{receipt.json()['data']['id']}",
                         headers=headers['warehouse']).json()['data']
    assert Decimal(pending['pending_quantity']) == 10
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        assert session.get(MaterialMovement, movement_id).shelved_quantity == 0
        assert session.scalar(select(func.count()).select_from(MaterialTransfer).where(
            MaterialTransfer.receipt_movement_id == movement_id)) == 0
    good = post(client, headers, 'material-putaways', {
        'receipt_movement_id': movement_id, 'location_id': data['location']['id'], 'quantity': '10'})
    assert good.status_code == 200, good.text
    batch_id = good.json()['data']['target_batch_id']
    with monkeypatch.context() as patch:
        def fail_audit(*args, **kwargs):
            raise RuntimeError('injected audit failure')
        patch.setattr(material_location_service, 'audit', fail_audit)
        result = post(client, headers, 'material-counts', {
            'batch_id': batch_id, 'book_quantity': '10', 'actual_quantity': '11', 'reason': '故障注入'})
    assert result.status_code == 500
    assert available(client, headers, material) == 10
    assert client.get('/api/material-counts', headers=headers['warehouse'],
                      params={'batch_id': batch_id}).json()['data']['total'] == 0
