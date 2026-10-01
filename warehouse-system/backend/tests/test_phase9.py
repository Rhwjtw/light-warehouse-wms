from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import MaterialBatch, StockDocument, InventoryTransaction, MaterialIssueRecord
from app.reconciliation import reconcile
from app.stock_service import business_today
from test_phase6 import context, scenario, post_material, detail, create_catalog
from test_phase7 import settle

pytestmark = pytest.mark.mysql


def test_large_decimal_bom_preview_keeps_all_digits(context):
    client, headers, _, prefix = context
    product = create_catalog(client, headers, 'products', prefix + 'largep', '大数精度验收', unit='盒')
    material = create_catalog(client, headers, 'materials', prefix + 'largem', '大数原料', unit='kg')
    maximum = '99999999999999.999999'
    response = client.post('/api/boms', headers=headers['admin'], json={
        'product_id': product['id'], 'version': 1, 'status': 'active',
        'items': [{'material_id': material['id'], 'quantity_per_unit': maximum, 'unit': 'g'}]})
    assert response.status_code == 200, response.text
    result = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': maximum})
    assert result.status_code == 200, result.text
    row = result.json()['data']['requirements'][0]
    with localcontext() as precision:
        precision.prec = 60
        expected = Decimal(maximum) * Decimal(maximum)
        assert Decimal(row['total_quantity']) == expected
        assert Decimal(row['suggested_quantity']) == expected / 1000


def test_v1_three_material_full_flow(context):
    data = scenario(context, 'v1', bom_quantities=('20', '5', '2'),
                    stock_batches=((0, '8', 20), (0, '25', 60), (1, '10', 60), (2, '5', 60)))
    client, headers = data['client'], data['headers']
    order = data['order']
    suggestion = client.get(f"/api/production-orders/{order['id']}/materials/{data['materials'][0]['id']}/fefo-suggestion",
                            headers=headers['warehouse']).json()['data']
    assert [(r['batch_id'], r['quantity']) for r in suggestion['suggestions']] == [(data['batches'][0], '8'), (data['batches'][1], '12')]
    payload = {'product_id': data['product']['id'], 'version': 1, 'status': 'inactive',
               'items': [{'material_id': m['id'], 'quantity_per_unit': q, 'unit': 'g', 'sort_order': i}
                         for i, (m, q) in enumerate(zip(data['materials'], ('20', '5', '2')), 1)]}
    assert client.put(f"/api/boms/{data['bom']['id']}", headers=headers['admin'], json=payload).status_code == 200
    payload.update(version=2, status='active'); payload['items'][0]['quantity_per_unit'] = '18'
    assert client.post('/api/boms', headers=headers['admin'], json=payload).status_code == 200
    for row, allocations in zip(order['materials'], [
        [{'batch_id': data['batches'][0], 'quantity': '8'}, {'batch_id': data['batches'][1], 'quantity': '12'}],
        [{'batch_id': data['batches'][2], 'quantity': '5'}], [{'batch_id': data['batches'][3], 'quantity': '2'}]]):
        assert post_material(data, 'issue', row['id'], allocations).status_code == 200
    for kind, quantity in [('supplement', '1.2'), ('return', '0.5')]:
        assert post_material(data, kind, order['materials'][0]['id'], [{'batch_id': data['batches'][1], 'quantity': quantity}]).status_code == 200
    result = settle(data)
    assert result.status_code == 200, result.text
    row = result.json()['data']['materials'][0]
    assert [row[k] for k in ('quantity_per_unit', 'planned_required_quantity', 'actual_used_quantity', 'final_theoretical_quantity', 'loss_quantity', 'loss_rate')] == ['20', '20', '20.7', '19.6', '1.1', '5.612244897959']
    frozen = detail(data)
    assert settle(data).status_code == 409
    for kind in ('issue', 'supplement', 'return'):
        assert post_material(data, kind, row['id'], [{'batch_id': data['batches'][1], 'quantity': '0.1'}]).status_code == 409
    for illegal in ({'planned_quantity': '1'}, {'bom_id': data['bom']['id']}):
        assert client.put(f"/api/production-orders/{order['id']}", headers=headers['admin'], json=illegal).status_code == 422
    assert detail(data) == frozen
    finished_batch = client.get(f"/api/finished-stock/{data['product']['id']}/batches", headers=headers['warehouse']).json()['data'][0]
    assert client.post('/api/finished-outbounds', headers=headers['warehouse'], json={
        'request_id': uuid4().hex, 'product_id': data['product']['id'], 'quantity': '10',
        'unit': '盒', 'business_date': str(business_today()),
        'allocations': [{'batch_id': finished_batch['id'], 'quantity': '10'}]}).status_code == 200
    with Session(bind=context[2]) as session:
        assert [session.get(MaterialBatch, i).remaining_quantity for i in data['batches']] == [Decimal('0'), Decimal('12.3'), Decimal('5'), Decimal('3')]
        report = reconcile(session)
        assert report['ok'], report


@pytest.mark.parametrize('kind', ['issue', 'supplement', 'return'])
@pytest.mark.parametrize('failure', ['document', 'detail', 'ledger', 'database', 'commit'])
def test_production_posting_all_failures_rollback(context, kind, failure):
    data = scenario(context, 'rollback', stock_batches=((0, '30', 30),))
    row_id = data['order']['materials'][0]['id']
    if kind == 'return':
        assert post_material(data, 'issue', row_id, [{'batch_id': data['batches'][0], 'quantity': '2'}]).status_code == 200
    def snapshot():
        with Session(bind=context[2]) as session:
            return {table.name: list(session.execute(select(table).order_by(*table.primary_key.columns)))
                    for table in Base.metadata.sorted_tables}
    before = snapshot()
    target = {'document': StockDocument, 'detail': MaterialIssueRecord, 'ledger': InventoryTransaction, 'database': InventoryTransaction}
    def fail(session, *args):
        if failure == 'commit':
            if not session.in_nested_transaction():
                raise OperationalError('test commit', {}, Exception('injected'))
        elif any(isinstance(row, target[failure]) for row in session.new):
            raise OperationalError('test write', {}, Exception('injected'))
    event_name = 'before_commit' if failure == 'commit' else 'before_flush'
    event.listen(Session, event_name, fail)
    try:
        response = post_material(data, kind, row_id, [{'batch_id': data['batches'][0], 'quantity': '1'}])
        assert response.status_code == 500
        assert 'test write' not in response.text and 'SQL' not in response.text
    finally:
        event.remove(Session, event_name, fail)
    assert snapshot() == before


def test_reconciliation_detects_unlogged_stock(context):
    data = scenario(context, 'corruption', stock_batches=((0, '3', 30),))
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        batch = session.get(MaterialBatch, data['batches'][0])
        batch.remaining_quantity = Decimal('2')
        session.flush()
        report = reconcile(session)
        assert any(p['type'] == 'material' and p['id'] == batch.id for p in report['problems'])
        session.rollback()


@pytest.mark.parametrize('kind', ['material_receipt', 'material_outbound', 'finished_receipt', 'finished_outbound', 'complete'])
@pytest.mark.parametrize('failure', ['document', 'ledger', 'database'])
def test_other_posting_failures_leave_all_tables_unchanged(context, kind, failure):
    data = scenario(context, 'atomic', stock_batches=((0, '30', 30),))
    client, headers = data['client'], data['headers']['warehouse']
    finished = {'request_id': uuid4().hex, 'product_id': data['product']['id'], 'quantity': '2',
                'unit': '盒', 'business_date': str(business_today())}
    assert client.post('/api/finished-receipts', headers=headers, json=finished).status_code == 200
    assert post_material(data, 'issue', data['order']['materials'][0]['id'],
                         [{'batch_id': data['batches'][0], 'quantity': '1'}]).status_code == 200
    def snapshot():
        with Session(bind=context[2]) as session:
            return {table.name: list(session.execute(select(table).order_by(*table.primary_key.columns)))
                    for table in Base.metadata.sorted_tables}
    before = snapshot()
    target = StockDocument if failure == 'document' else InventoryTransaction
    def fail(session, *args):
        if any(isinstance(row, target) for row in session.new):
            raise OperationalError('injected database failure', {}, Exception('injected'))
    event.listen(Session, 'before_flush', fail)
    try:
        if kind == 'complete':
            response = settle(data)
        elif kind.startswith('finished'):
            response = client.post('/api/' + kind.replace('_', '-') + 's', headers=headers,
                                   json={**finished, 'request_id': uuid4().hex, 'quantity': '1'})
        else:
            payload = {'request_id': uuid4().hex, 'material_id': data['materials'][0]['id'], 'quantity': '1', 'unit': 'kg'}
            if kind == 'material_receipt':
                payload.update(batch_no='extra', production_date=str(business_today()), expiry_date=str(business_today()),
                               supplier_id=data['supplier']['id'])
            else:
                payload['batch_id'] = data['batches'][0]
            response = client.post('/api/' + kind.replace('_', '-') + 's', headers=headers, json=payload)
        assert response.status_code == 500, response.text
    finally:
        event.remove(Session, 'before_flush', fail)
    assert snapshot() == before
