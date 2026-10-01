"""Third batch quantities are checked against real posting facts, not HTTP status alone."""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from sqlalchemy import select, func, event

from app.models import FinishedBatch, FinishedReceipt, InventoryTransaction, StockDocument, MaterialBatch
from app.reconciliation import reconcile
from app.stock_service import business_today
from test_phase6 import context, scenario, post_material, detail, create_catalog
from test_phase7 import setup_order, settle, balance
from test_reversals import reverse

pytestmark = pytest.mark.mysql


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def batches(data):
    return ok(data['client'].get(f"/api/finished-stock/{data['product']['id']}/batches", headers=data['headers']['admin']))


def trace(data, kind, identity, role='accountant'):
    return ok(data['client'].get(f'/api/traceability/{kind}/{identity}', headers=data['headers'][role]))


def outbound(data, allocations, quantity):
    return data['client'].post('/api/finished-outbounds', headers=data['headers']['warehouse'], json={
        'request_id': uuid4().hex, 'product_id': data['product']['id'], 'quantity': quantity,
        'unit': '盒', 'business_date': str(business_today()), 'allocations': allocations})


def test_multi_batch_complete_outbound_aggregate_and_inverse(context):
    data = setup_order(context, 'third-a')
    ok(data['client'].post('/api/finished-receipts', headers=data['headers']['warehouse'], json={
        'request_id': uuid4().hex, 'product_id': data['product']['id'], 'quantity': '7', 'unit': '盒', 'business_date': str(business_today())}))
    assert settle(data, '1000', finished_batches=[{'batch_no': 'FG-A', 'quantity': '600'}, {'batch_no': 'FG-B', 'quantity': '399'}]).status_code == 422
    assert balance(data) == 7 and detail(data)['status'] == 'in_progress'
    ok(settle(data, '1000', finished_batches=[{'batch_no': 'FG-A', 'quantity': '600'}, {'batch_no': 'FG-B', 'quantity': '400'}]))
    stock = batches(data)
    assert [(r['stock_scope'], Decimal(r['quantity'])) for r in stock] == [('aggregate', Decimal(7)), ('batch', Decimal(600)), ('batch', Decimal(400))]
    allocations = [{'batch_id': stock[1]['id'], 'quantity': '60'}, {'batch_id': stock[2]['id'], 'quantity': '40'}]
    doc = ok(outbound(data, allocations, '100'))
    assert doc['quantity'] == '100.000000' and len(doc['allocations']) == 2
    heads = ok(data['client'].get('/api/finished-receipts', params={'product_id': data['product']['id']}, headers=data['headers']['admin']))
    assert heads['total'] == 2
    assert Decimal(heads['items'][0]['quantity']) == 1000 and len(heads['items'][0]['allocations']) == 2
    ledger = ok(data['client'].get('/api/finished-transactions', params={'product_id': data['product']['id']}, headers=data['headers']['admin']))
    assert ledger['total'] == 5 and len({r['transaction_id'] for r in ledger['items']}) == 5
    outgoing_heads = ok(data['client'].get('/api/finished-outbounds', params={'product_id': data['product']['id']}, headers=data['headers']['admin']))
    assert outgoing_heads['total'] == 1 and Decimal(outgoing_heads['items'][0]['change_quantity']) == -100
    assert [Decimal(b['quantity']) for b in batches(data)] == [7, 540, 360]
    forward = trace(data, 'material-batches', data['batches'][0])
    assert {b['batch_no'] for b in forward['production_orders'][0]['finished_batches']} == {'FG-A', 'FG-B'}
    backward = trace(data, 'finished-batches', stock[1]['id'])
    material = backward['production_orders'][0]['materials'][0]
    assert Decimal(material['net_used_quantity']) == 20 and material['supplier_id'] == data['supplier']['id']
    assert backward['subject']['current_quantity'] == '540.000000'
    assert doc['id'] in {d['id'] for d in backward['documents']}
    ok(reverse(data['client'], data['headers'], doc['id']))
    assert [Decimal(b['quantity']) for b in batches(data)] == [7, 600, 400]
    assert next(d for d in trace(data, 'finished-batches', stock[1]['id'])['documents'] if d['id'] == doc['id'])['status'] == 'reversed'
    historical = trace(data, 'finished-batches', stock[0]['id'])
    assert historical['production_orders'] == [] and historical['subject']['batch_no'] is None
    assert outbound(data, allocations, '101').status_code == 422
    with Session(bind=context[2]) as session:
        persisted = [session.get(FinishedBatch, b['id']) for b in stock]
        assert persisted[0].aggregate_product_id == data['product']['id'] and persisted[0].real_batch_no is None
        assert all(b.aggregate_product_id is None and b.real_batch_no == b.batch_no for b in persisted[1:])
        receipts = list(session.scalars(select(FinishedReceipt).where(FinishedReceipt.production_order_id == data['order']['id'])))
        assert len(receipts) == 2 and len({r.document_id for r in receipts}) == 1
        assert reconcile(session)['problems'] == []


def test_actual_split_supplement_return_and_reversal(context):
    data = scenario(context, 'third-b', stock_batches=((0, '10', 30), (0, '10', 30), (0, '10', 30)))
    row = data['order']['materials'][0]['id']
    issue = ok(post_material(data, 'issue', row, [{'batch_id': data['batches'][0], 'quantity': '6'}, {'batch_id': data['batches'][1], 'quantity': '4'}]))
    supplement = ok(post_material(data, 'supplement', row, [{'batch_id': data['batches'][2], 'quantity': '1'}]))
    returned = ok(post_material(data, 'return', row, [{'batch_id': data['batches'][0], 'quantity': '0.5'}]))
    used = trace(data, 'production-orders', data['order']['id'])['production_orders'][0]['materials']
    assert [Decimal(r['net_used_quantity']) for r in used] == [Decimal('5.5'), Decimal('4'), Decimal('1')]
    assert reverse(data['client'], data['headers'], issue['id']).status_code == 409
    ok(reverse(data['client'], data['headers'], returned['id']))
    assert Decimal(detail(data)['materials'][0]['returned_quantity']) == 0
    ok(reverse(data['client'], data['headers'], supplement['id']))
    assert trace(data, 'material-batches', data['batches'][2])['production_orders'] == []
    ok(reverse(data['client'], data['headers'], issue['id']))
    summary = detail(data)['materials'][0]
    assert [Decimal(summary[k]) for k in ('issued_quantity', 'supplemented_quantity', 'returned_quantity', 'net_issued_quantity')] == [0, 0, 0, 0]
    assert all(r['is_reversed'] for r in detail(data)['material_records'])
    assert len(trace(data, 'material-batches', data['batches'][0])['excluded_orders']) == 1
    assert settle(data).status_code == 409
    # Reversed issue cannot be used as a new return entitlement.
    assert post_material(data, 'return', row, [{'batch_id': data['batches'][0], 'quantity': '1'}]).status_code == 409
    ok(post_material(data, 'issue', row, [{'batch_id': data['batches'][1], 'quantity': '2'}]))
    ok(settle(data))
    assert reverse(data['client'], data['headers'], detail(data)['material_records'][0]['document_id']).status_code == 409
    with Session(bind=context[2]) as session:
        assert [session.get(MaterialBatch, b).remaining_quantity for b in data['batches']] == [10, 8, 10]
        assert reconcile(session)['problems'] == []


@pytest.mark.parametrize('specs', [
    [{'batch_no': '', 'quantity': '1000'}], [{'batch_no': 'X', 'quantity': '0'}],
    [{'batch_no': 'X', 'quantity': '500'}, {'batch_no': 'X', 'quantity': '500'}],
    [{'batch_no': 'X', 'quantity': '1000.0000001'}],
])
def test_invalid_finished_batch_atomic(context, specs):
    data = setup_order(context, 'invalid' + uuid4().hex[:4])
    assert settle(data, '1000', finished_batches=specs).status_code == 422
    assert batches(data) == [] and detail(data)['status'] == 'in_progress'


def test_trace_permissions_readonly_and_search(context):
    data = setup_order(context, 'third-perms')
    for role in data['headers']:
        result = trace(data, 'material-batches', data['batches'][0], role)
        assert Decimal(result['production_orders'][0]['materials'][0]['net_used_quantity']) == 20
        found = ok(data['client'].get('/api/traceability/search', params={'q': 'third-perms'}, headers=data['headers'][role]))
        assert any(r['entry_type'] == 'material' and r['supplier_name'] == data['supplier']['name'] for r in found['items'])
    assert data['client'].get('/api/traceability/search', params={'q': 'x'}).status_code == 401
    assert data['client'].post('/api/traceability/material-batches/1', headers=data['headers']['admin']).status_code == 405


def test_logical_batch_split_transfer_multiple_receipts_suppliers_orders(context):
    from datetime import timedelta
    data = scenario(context, 'logical14')
    client, headers = data['client'], data['headers']
    loc2 = create_catalog(client, headers, 'locations', '', 'logical-target14')
    supplier2 = create_catalog(client, headers, 'suppliers', uuid4().hex, '第二供应商14')
    def receipt(supplier, quantity='10'):
        document = ok(client.post('/api/material-receipts', headers=headers['warehouse'], json={
            'request_id': uuid4().hex, 'material_id': data['materials'][0]['id'], 'batch_no': 'SAME-RM14',
            'quantity': quantity, 'unit': ' kg ', 'supplier_id': supplier,
            'production_date': str(business_today() - timedelta(days=1)), 'expiry_date': str(business_today() + timedelta(days=30))}))
        movement = ok(client.get(f"/api/material-receipts/{document['id']}", headers=headers['warehouse']))['receipt_movement_id']
        return document, movement
    original, movement = receipt(data['supplier']['id'])
    def shelf(location, quantity, movement_id=movement):
        return ok(client.post('/api/material-putaways', headers=headers['warehouse'], json={
            'request_id': uuid4().hex, 'receipt_movement_id': movement_id, 'location_id': location, 'quantity': quantity}))
    a = shelf(data['location']['id'], '6')['target_batch_id']
    b = shelf(loc2['id'], '4')['target_batch_id']
    moved = ok(client.post('/api/material-transfers', headers=headers['warehouse'], json={
        'request_id': uuid4().hex, 'source_batch_id': a, 'target_location_id': loc2['id'], 'quantity': '1'}))
    assert moved['target_batch_id'] == b
    second, movement2 = receipt(data['supplier']['id'])
    assert shelf(data['location']['id'], '10', movement2)['target_batch_id'] == a
    other, movement3 = receipt(supplier2['id'])
    c = shelf(data['location']['id'], '10', movement3)['target_batch_id']
    row = data['order']['materials'][0]['id']
    ok(post_material(data, 'issue', row, [{'batch_id': a, 'quantity': '2'}, {'batch_id': b, 'quantity': '3'}, {'batch_id': c, 'quantity': '1'}]))
    order2 = ok(client.post('/api/production-orders', headers=headers['admin'], json={'finished_product_id': data['product']['id'], 'planned_quantity': '1000'}))
    another = {**data, 'order': order2}
    ok(post_material(another, 'issue', order2['materials'][0]['id'], [{'batch_id': b, 'quantity': '1'}]))
    ok(settle(data)); ok(settle(another))
    for locator in (a, b, original['batch_id'], second['batch_id']):
        result = trace(data, 'material-batches', locator)
        assert len(result['subject']['physical_batch_ids']) == 4
        assert result['subject']['supplier_id'] == data['supplier']['id']
        assert {o['id'] for o in result['production_orders']} == {data['order']['id'], order2['id']}
        assert Decimal(result['production_orders'][0]['selected_material_usage']['net_used_quantity']) == 5
        assert any('多次入库' in w for w in result['warnings'])
    found = ok(client.get('/api/traceability/search', params={'q': 'SAME-RM14'}, headers=headers['accountant']))['items']
    assert len(found) == 2 and {r['supplier_id'] for r in found} == {data['supplier']['id'], supplier2['id']}
    assert len(trace(data, 'material-batches', c)['subject']['physical_batch_ids']) == 2
    with Session(bind=context[2]) as session:
        assert reconcile(session)['problems'] == []


def test_duplicate_collation_atomic_and_complete_fault(context):
    data = setup_order(context, 'atomic14')
    assert settle(data, '1000', finished_batches=[{'batch_no': 'fg-c', 'quantity': '500'}, {'batch_no': 'FG-C', 'quantity': '500'}]).status_code == 409
    assert batches(data) == [] and detail(data)['actual_quantity'] is None
    count = 0
    def fail_second(mapper, conn, row):
        nonlocal count
        if row.kind == 'finished_receipt':
            count += 1
            if count == 2:
                raise RuntimeError('second batch ledger fails')
    event.listen(InventoryTransaction, 'before_insert', fail_second)
    try:
        assert settle(data, '1000', finished_batches=[{'batch_no': 'FG-1', 'quantity': '600'}, {'batch_no': 'FG-2', 'quantity': '400'}]).status_code == 500
    finally:
        event.remove(InventoryTransaction, 'before_insert', fail_second)
    assert batches(data) == [] and detail(data)['actual_quantity'] is None
    ok(settle(data, '1000'))
    stock = batches(data)[0]
    outgoing = ok(outbound(data, [{'batch_id': stock['id'], 'quantity': '1000'}], '1000'))
    assert trace(data, 'finished-batches', stock['id'])['subject']['current_quantity'] == '0.000000'
    assert Decimal(trace(data, 'finished-batches', stock['id'])['production_orders'][0]['materials'][0]['net_used_quantity']) == 20
    with Session(bind=context[2]) as session:
        assert reconcile(session)['problems'] == []


@pytest.mark.parametrize('kind', ['issue', 'supplement', 'return'])
def test_multirow_material_reversal_fault_rolls_back_projection(context, kind):
    data = scenario(context, 'rv-fault14-' + kind, stock_batches=((0, '10', 30), (0, '10', 30)))
    material_id = data['order']['materials'][0]['id']
    allocations = [{'batch_id': b, 'quantity': '2'} for b in data['batches']]
    documents = {}
    for action in ('issue', 'supplement', 'return'):
        documents[action] = ok(post_material(data, action, material_id, allocations))['id']
    before = detail(data)
    with Session(bind=context[2]) as session:
        stock = [session.get(MaterialBatch, b).remaining_quantity for b in data['batches']]
    count = 0
    def fail_second(mapper, conn, row):
        nonlocal count
        if row.kind == 'reversal':
            count += 1
            if count == 2:
                raise RuntimeError('second inverse ledger fails')
    event.listen(InventoryTransaction, 'before_insert', fail_second)
    try:
        assert reverse(data['client'], data['headers'], documents[kind]).status_code == 500
    finally:
        event.remove(InventoryTransaction, 'before_insert', fail_second)
    assert detail(data) == before
    with Session(bind=context[2]) as session:
        assert [session.get(MaterialBatch, b).remaining_quantity for b in data['batches']] == stock
        assert session.scalar(select(StockDocument.id).where(StockDocument.reverses_document_id == documents[kind])) is None
        assert reconcile(session)['problems'] == []


def test_logical_null_dates_units_and_history_labels_not_guessed():
    from types import SimpleNamespace
    from app.traceability_service import logical_key
    base = dict(material_id=1, batch_no='RM', supplier_id=2, production_date=None, expiry_date=None, unit='kg')
    key = logical_key(SimpleNamespace(**base))
    assert key == (1, 'RM', 2, None, None, 'kg')
    for change in ({'batch_no':'rm'}, {'batch_no':'RM '}, {'supplier_id':None}, {'unit':'g'}, {'production_date':business_today()}):
        assert logical_key(SimpleNamespace(**{**base, **change})) != key


def test_incomplete_inverse_does_not_neutralize_quantities(context):
    # Fault injection simulates corrupted inverse facts, never edits existing history.
    data = setup_order(context, 'partial14')
    original = detail(data)['material_records'][0]['document_id']
    def wrong_link(mapper, conn, row):
        if row.kind == 'reversal':
            row.source_location_id = row.target_location_id = None
    event.listen(InventoryTransaction, 'before_insert', wrong_link)
    try:
        ok(reverse(data['client'], data['headers'], original))
    finally:
        event.remove(InventoryTransaction, 'before_insert', wrong_link)
    result = trace(data, 'material-batches', data['batches'][0])
    assert Decimal(result['production_orders'][0]['selected_material_usage']['net_used_quantity']) == 20
    assert next(d for d in result['documents'] if d['id'] == original)['status'] == 'incomplete_reversal'
    with Session(bind=context[2]) as session:
        assert not reconcile(session)['ok']
    # Fixture outer rollback isolates this intentionally malformed scenario from clean regression.
