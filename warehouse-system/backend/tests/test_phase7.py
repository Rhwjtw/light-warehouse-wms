from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.models import (BomItem, FinishedBatch, FinishedReceipt, InventoryTransaction,
                        OperationLog, ProductionOrderMaterial, StockDocument)
from app.settlement_service import loss_values
from app.stock_service import business_today
from test_phase6 import context, scenario, post_material, detail

pytestmark = pytest.mark.mysql


def setup_order(context, suffix='settle', quantity='20', **kwargs):
    data = scenario(context, suffix, stock_batches=((0, '30', 30),), **kwargs)
    assert post_material(data, 'issue', data['order']['materials'][0]['id'],
                         [{'batch_id': data['batches'][0], 'quantity': quantity}]).status_code == 200
    return data


def settle(data, quantity='980', action='complete', role='workshop_director', **extra):
    return data['client'].post(f"/api/production-orders/{data['order']['id']}/{action}",
                               headers=data['headers'][role], json={'actual_quantity': quantity, **extra})


def balance(data):
    result = data['client'].get(f"/api/finished-stock/{data['product']['id']}", headers=data['headers']['admin'])
    return Decimal(result.json()['data']['quantity'])


def test_standard_full_settlement_preview_stock_ledger_and_freeze(context):
    data = setup_order(context)
    for kind, quantity in [('supplement', '1.2'), ('return', '0.5')]:
        assert post_material(data, kind, data['order']['materials'][0]['id'],
                             [{'batch_id': data['batches'][0], 'quantity': quantity}]).status_code == 200
    receipt = data['client'].post('/api/finished-receipts', headers=data['headers']['warehouse'], json={
        'request_id': uuid4().hex, 'product_id': data['product']['id'], 'quantity': '100',
        'unit': '盒', 'business_date': str(business_today())})
    assert receipt.status_code == 200
    before = detail(data)
    for _ in range(2):
        preview = settle(data, action='settlement-preview')
        assert preview.status_code == 200, preview.text
        assert detail(data) == before and balance(data) == 100
    result = settle(data)
    assert result.status_code == 200, result.text
    order = result.json()['data']
    row = order['materials'][0]
    assert (row['planned_required_quantity'], row['final_theoretical_quantity'], row['actual_used_quantity'],
            row['loss_quantity'], row['loss_rate']) == ('20', '19.6', '20.7', '1.1', '5.612244897959')
    assert order['actual_quantity'] == '980' and order['status'] == 'completed' and order['completed_at']
    assert balance(data) == 1080
    assert settle(data).status_code == 409 and balance(data) == 1080
    ledger = data['client'].get('/api/finished-transactions', headers=data['headers']['warehouse'],
                                params={'q': order['order_no']}).json()['data']
    assert ledger['total'] == 1
    assert ledger['items'][0]['business_type'] == 'production'
    assert ledger['items'][0]['production_order_id'] == order['id']
    assert Decimal(ledger['items'][0]['change_quantity']) == 980
    for kind in ('issue', 'supplement', 'return'):
        assert post_material(data, kind, row['id'], [{'batch_id': data['batches'][0], 'quantity': '0.1'}]).status_code == 409
    assert data['client'].put(f"/api/production-orders/{order['id']}", headers=data['headers']['admin'],
                              json={'notes': 'forbidden'}).status_code == 409
    assert data['client'].post(f"/api/production-orders/{order['id']}/cancel", headers=data['headers']['admin']).status_code == 409


@pytest.mark.parametrize('quantity,theoretical', [('1000','20'), ('900','18'), ('1050','21')])
def test_actual_quantity_relative_to_plan(context, quantity, theoretical):
    data = setup_order(context, quantity)
    result = settle(data, quantity)
    assert result.status_code == 200, result.text
    assert result.json()['data']['materials'][0]['final_theoretical_quantity'] == theoretical


def test_old_bom_snapshot_and_history_are_fixed(context):
    data = setup_order(context, 'snapshot')
    old = data['bom']
    payload = {'product_id': data['product']['id'], 'version': 1, 'status': 'inactive',
               'items': [{'material_id': data['materials'][0]['id'], 'quantity_per_unit': '20', 'unit': 'g', 'sort_order': 1}]}
    assert data['client'].put(f"/api/boms/{old['id']}", headers=data['headers']['admin'], json=payload).status_code == 200
    payload.update(version=2, status='active')
    payload['items'][0]['quantity_per_unit'] = '18'
    assert data['client'].post('/api/boms', headers=data['headers']['admin'], json=payload).status_code == 200
    assert settle(data).json()['data']['materials'][0]['final_theoretical_quantity'] == '19.6'
    frozen = detail(data)['materials']
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        bom_item = session.scalar(select(BomItem).where(BomItem.bom_id == old['id']))
        bom_item.quantity_per_unit = Decimal('15')
        session.commit()
    assert detail(data)['materials'] == frozen


def test_negative_loss_and_decimal(context):
    from app.production_service import decimal_text
    assert decimal_text(Decimal('9999999999999999999999.123456789012')) == '9999999999999999999999.123456789012'
    data = setup_order(context, 'negative', quantity='19')
    row = settle(data, '1000').json()['data']['materials'][0]
    assert row['loss_quantity'] == '-1' and row['loss_rate'] == '-5'
    data = setup_order(context, 'decimal', quantity='0.1', bom_quantities=('0.1',))
    post_material(data, 'supplement', data['order']['materials'][0]['id'], [{'batch_id': data['batches'][0], 'quantity': '0.2'}])
    row = settle(data, '1000').json()['data']['materials'][0]
    assert (row['actual_used_quantity'], row['final_theoretical_quantity'], row['loss_quantity']) == ('0.3', '0.1', '0.2')


def test_zero_theoretical_never_divides_by_zero(context, monkeypatch):
    assert loss_values(Decimal('0'), Decimal('1')) == (Decimal('1'), None)
    import app.settlement_service as service
    monkeypatch.setattr(service, 'convert_quantity', lambda *args, **kwargs: Decimal('0'))
    data = setup_order(context, 'zero')
    result = settle(data)
    assert result.status_code == 200, result.text
    assert result.json()['data']['materials'][0]['loss_rate'] is None


@pytest.mark.parametrize('failure', ['ledger','receipt','audit','commit'])
def test_atomic_rollback(context, failure, monkeypatch):
    data = setup_order(context, 'rollback'+failure)
    models = (FinishedBatch, FinishedReceipt, InventoryTransaction, StockDocument, OperationLog)
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        counts = {model: session.scalar(select(func.count()).select_from(model)) for model in models}
    def fail(session, *args):
        if failure == 'commit':
            if not session.in_nested_transaction():
                raise RuntimeError('forced final commit failure')
        elif any(isinstance(row, FinishedReceipt if failure == 'receipt' else InventoryTransaction) for row in session.new):
            raise RuntimeError('forced posting failure')
    event_name = 'before_commit' if failure == 'commit' else 'before_flush'
    if failure == 'audit':
        import app.settlement_service as service
        monkeypatch.setattr(service, 'audit', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('forced audit')))
    else:
        event.listen(Session, event_name, fail)
    try:
        assert settle(data).status_code == 500
    finally:
        if failure != 'audit':
            event.remove(Session, event_name, fail)
    order = detail(data)
    assert order['status'] == 'in_progress' and order['actual_quantity'] is None and order['completed_at'] is None
    assert all(order['materials'][0][field] is None for field in ('final_theoretical_quantity','actual_used_quantity','loss_quantity','loss_rate'))
    assert balance(data) == 0
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        for model, count in counts.items():
            assert session.scalar(select(func.count()).select_from(model)) == count


def test_cancelled_no_material_and_permissions(context):
    data = scenario(context, 'empty')
    assert settle(data).status_code == 409
    data['client'].post(f"/api/production-orders/{data['order']['id']}/cancel", headers=data['headers']['admin'])
    assert settle(data).status_code == 409
    data = setup_order(context, 'permissions')
    assert settle(data, role='workshop').status_code == 403
    assert settle(data, action='settlement-preview', role='workshop').status_code == 200


@pytest.mark.parametrize('quantity', ['0','-1','NaN','Infinity','0.0000001','100000000000000',0.1,True])
def test_invalid_actual_quantities(context, quantity):
    data = setup_order(context, 'invalid')
    assert settle(data, quantity).status_code == 422
    assert detail(data)['actual_quantity'] is None and balance(data) == 0


def test_injection_statistics_filters_and_pagination(context):
    data = setup_order(context, 'stats', bom_quantities=('20','5'))
    assert settle(data, loss_quantity='0').status_code == 422
    assert settle(data).status_code == 200
    params = {'order_no': data['order']['order_no'], 'page_size': 1,
              'product': data['product']['name'], 'date_from': str(business_today()), 'date_to': str(business_today())}
    def query(**extra):
        return data['client'].get('/api/losses', headers=data['headers']['workshop'], params={**params, **extra})
    first = query().json()['data']; second = query(page=2).json()['data']
    assert first['total'] == 2 and len(first['items']) == 1 and first['items'][0]['id'] != second['items'][0]['id']
    assert query(material_id=data['materials'][0]['id']).json()['data']['total'] == 1
    assert query(material='does-not-exist').json()['data']['total'] == 0
    assert query(date_to=str(business_today()-timedelta(days=1))).status_code == 422
    assert query(date_from=str(business_today()+timedelta(days=1)),date_to=str(business_today()+timedelta(days=2))).json()['data']['total'] == 0


def test_supplement_only_full_return_and_old_request_are_frozen(context):
    data = scenario(context, 'supponly', stock_batches=((0, '30', 30),))
    key = uuid4().hex
    allocations = [{'batch_id': data['batches'][0], 'quantity': '1'}]
    row_id = data['order']['materials'][0]['id']
    assert post_material(data, 'supplement', row_id, allocations, request_id=key).status_code == 200
    assert post_material(data, 'return', row_id, allocations).status_code == 200
    result = settle(data)
    assert result.status_code == 200
    assert result.json()['data']['materials'][0]['actual_used_quantity'] == '0'
    assert post_material(data, 'supplement', row_id, allocations, request_id=key).status_code == 409


def test_summary_corruption_and_theoretical_capacity_fail_cleanly(context):
    data = setup_order(context, 'capacity')
    assert settle(data, '99999999999999').status_code == 200  # 20g scales down to kg
    data = setup_order(context, 'overflow', bom_quantities=('2000',))
    assert settle(data, '99999999999999.999999').status_code == 422
    assert balance(data) == 0 and detail(data)['actual_quantity'] is None
    data = setup_order(context, 'corrupt')
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        session.get(ProductionOrderMaterial, data['order']['materials'][0]['id']).issued_quantity = Decimal('19')
        session.commit()
    assert settle(data).status_code == 409 and balance(data) == 0


def test_product_units_inherited_and_unique_receipt_constraint(context):
    from app.models import FinishedProduct, ProductionOrder
    from sqlalchemy.exc import IntegrityError
    data = setup_order(context, 'bottle')
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        session.get(ProductionOrder, data['order']['id']).unit = '瓶'
        session.get(FinishedProduct, data['product']['id']).unit = '瓶'
        session.commit()
    result = settle(data)
    assert result.status_code == 200 and result.json()['data']['finished_product_unit'] == '瓶'
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        original = session.scalar(select(FinishedReceipt).where(FinishedReceipt.production_order_id == data['order']['id']))
        document = StockDocument(document_no='TEST-'+uuid4().hex,request_id=uuid4().hex,kind='finished_receipt',
                                 business_type='production',operator_id=data['order']['created_by'])
        session.add(document); session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(FinishedReceipt(document_id=document.id, production_order_id=data['order']['id'],
                                            batch_id=original.batch_id,quantity=Decimal('1')))
                session.flush()
