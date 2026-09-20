from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import engine, get_session
from app.main import app
from app.models import (
    BomHeader, InventoryTransaction, MaterialBatch, MaterialIssueRecord,
    ProductionOrder, ProductionOrderMaterial, Role, StockDocument, User,
)
from app.stock_service import business_today

pytestmark = pytest.mark.mysql
PASSWORD = 'Test-Password-For-Tests-Only!'


@pytest.fixture(scope='module')
def context():
    connection = engine.connect()
    outer = connection.begin()
    prefix = 'prod_' + uuid4().hex[:8]

    def db():
        with Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
            with session.begin():
                yield session

    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        usernames = {}
        for code in ('admin', 'warehouse', 'workshop'):
            role = session.scalar(select(Role).where(Role.code == code))
            user = User(username=f'{prefix}_{code}', display_name=code, role_id=role.id,
                        password_hash=hash_password(PASSWORD))
            session.add(user)
            session.flush()
            usernames[code] = user.username
        session.commit()
    app.dependency_overrides[get_session] = db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = {}
            for code, username in usernames.items():
                response = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD})
                headers[code] = {'Authorization': 'Bearer ' + response.json()['data']['access_token']}
            yield client, headers, connection, prefix
    finally:
        app.dependency_overrides.clear()
        outer.rollback()
        connection.close()


def create_catalog(client, headers, path, code, name, **extra):
    response = client.post(f'/api/{path}', headers=headers['admin'], json={'code': code, 'name': name, **extra})
    assert response.status_code == 200, response.text
    return response.json()['data']


def scenario(context, suffix, *, bom_quantities=('20',), stock_batches=()):
    client, headers, _, prefix = context
    token = f'{prefix}{suffix}{uuid4().hex[:4]}'
    product = create_catalog(client, headers, 'products', token + 'p', '测试面霜' + suffix, unit='盒')
    supplier = create_catalog(client, headers, 'suppliers', token + 's', '供应商' + suffix)
    location = create_catalog(client, headers, 'locations', token + 'l', '库位' + suffix)
    materials = []
    for index, quantity in enumerate(bom_quantities):
        material = create_catalog(client, headers, 'materials', token + f'm{index}', f'原料{suffix}{index}', unit='kg')
        materials.append(material)
    bom_response = client.post('/api/boms', headers=headers['admin'], json={
        'product_id': product['id'], 'version': 1, 'status': 'active',
        'items': [{'material_id': material['id'], 'quantity_per_unit': quantity,
                   'unit': 'g', 'sort_order': index}
                  for index, (material, quantity) in enumerate(zip(materials, bom_quantities), 1)],
    })
    assert bom_response.status_code == 200, bom_response.text
    batches = []
    for index, row in enumerate(stock_batches):
        material_index, quantity, expiry_delta = row
        receipt = client.post('/api/material-receipts', headers=headers['warehouse'], json={
            'request_id': uuid4().hex,
            'material_id': materials[material_index]['id'],
            'batch_no': f'{suffix}-B{index + 1}',
            'quantity': quantity,
            'unit': 'kg',
            'production_date': str(business_today() - timedelta(days=10)),
            'expiry_date': str(business_today() + timedelta(days=expiry_delta)),
            'supplier_id': supplier['id'],
            'location_id': location['id'],
        })
        assert receipt.status_code == 200, receipt.text
        batches.append(receipt.json()['data']['batch_id'])
    order_response = client.post('/api/production-orders', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1000', 'notes': '第六阶段测试',
    })
    assert order_response.status_code == 200, order_response.text
    return {
        'client': client, 'headers': headers, 'product': product, 'materials': materials,
        'bom': bom_response.json()['data'], 'batches': batches, 'order': order_response.json()['data'],
        'supplier': supplier, 'location': location,
    }


def post_material(data, kind, order_material_id, allocations, *, role='warehouse', unit='kg', request_id=None):
    return data['client'].post(
        f"/api/production-orders/{data['order']['id']}/{kind}", headers=data['headers'][role],
        json={'request_id': request_id or uuid4().hex, 'order_material_id': order_material_id,
              'unit': unit, 'allocations': allocations},
    )


def detail(data):
    return data['client'].get(
        f"/api/production-orders/{data['order']['id']}", headers=data['headers']['warehouse'],
    ).json()['data']


def test_create_order_saves_exact_theoretical_requirements(context):
    data = scenario(context, 'create', bom_quantities=('20', '5'))
    order = data['order']
    assert order['order_no'].startswith('PO' + business_today().strftime('%Y%m%d'))
    assert order['status'] == 'ready' and order['bom_version'] == 1
    assert [(row['quantity_per_unit'], row['bom_unit'], row['planned_required_quantity'], row['base_unit'])
            for row in order['materials']] == [('20', 'g', '20', 'kg'), ('5', 'g', '5', 'kg')]


def test_bom_snapshot_is_immutable_after_new_active_version(context):
    data = scenario(context, 'snapshot')
    old = data['bom']
    deactivate = {**old, 'status': 'inactive'}
    deactivate['items'] = [{
        'material_id': row['material_id'], 'quantity_per_unit': row['quantity_per_unit'],
        'unit': row['unit'], 'sort_order': row['sort_order'], 'notes': row['notes'],
    } for row in old['items']]
    for key in ('id', 'product_code', 'product_name', 'product_unit', 'created_at', 'updated_at'):
        deactivate.pop(key, None)
    assert data['client'].put(f"/api/boms/{old['id']}", headers=data['headers']['admin'], json=deactivate).status_code == 200
    assert data['client'].post('/api/boms', headers=data['headers']['admin'], json={
        'product_id': data['product']['id'], 'version': 2, 'status': 'active',
        'items': [{'material_id': data['materials'][0]['id'], 'quantity_per_unit': '18', 'unit': 'g'}],
    }).status_code == 200
    old_order = detail(data)
    assert old_order['bom_version'] == 1
    assert old_order['materials'][0]['quantity_per_unit'] == '20'
    assert old_order['materials'][0]['planned_required_quantity'] == '20'


def test_create_order_does_not_change_inventory(context):
    client, headers, connection, prefix = context
    data = scenario(context, 'nostock', stock_batches=((0, '30', 30),))
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        batch = session.get(MaterialBatch, data['batches'][0])
        transactions = session.scalar(select(func.count()).select_from(InventoryTransaction)
                                      .where(InventoryTransaction.material_batch_id == batch.id))
        assert batch.remaining_quantity == Decimal('30') and transactions == 1


def test_normal_issue_updates_batch_summary_status_and_ledger(context):
    data = scenario(context, 'issue', stock_batches=((0, '30', 30),))
    material = data['order']['materials'][0]
    response = post_material(data, 'issue', material['id'], [{'batch_id': data['batches'][0], 'quantity': '20'}])
    assert response.status_code == 200, response.text
    current = detail(data)
    assert current['status'] == 'in_progress'
    assert current['materials'][0]['issued_quantity'] == '20'
    assert current['materials'][0]['net_issued_quantity'] == '20'
    record = current['material_records'][0]
    assert record['kind'] == 'issue' and record['change_quantity'] == '-20' and record['after_quantity'] == '10'
    ledger = data['client'].get('/api/material-transactions', headers=data['headers']['warehouse'],
                                params={'q': data['order']['order_no'], 'kind': 'issue'}).json()['data']
    assert ledger['total'] == 1
    assert ledger['items'][0]['production_order_no'] == data['order']['order_no']


def test_split_issue_accumulates(context):
    data = scenario(context, 'split', stock_batches=((0, '30', 30),))
    material_id = data['order']['materials'][0]['id']
    assert post_material(data, 'issue', material_id, [{'batch_id': data['batches'][0], 'quantity': '8'}]).status_code == 200
    assert post_material(data, 'issue', material_id, [{'batch_id': data['batches'][0], 'quantity': '12'}]).status_code == 200
    assert detail(data)['materials'][0]['issued_quantity'] == '20'


def test_supplement_is_separate_and_reduces_stock(context):
    data = scenario(context, 'supplement', stock_batches=((0, '30', 30),))
    material_id = data['order']['materials'][0]['id']
    post_material(data, 'issue', material_id, [{'batch_id': data['batches'][0], 'quantity': '20'}])
    response = post_material(data, 'supplement', material_id, [{'batch_id': data['batches'][0], 'quantity': '1.2'}])
    assert response.status_code == 200, response.text
    current = detail(data)
    row = current['materials'][0]
    assert (row['issued_quantity'], row['supplemented_quantity'], row['net_issued_quantity']) == ('20', '1.2', '21.2')
    assert [record['kind'] for record in current['material_records'][:2]] == ['supplement', 'issue']
    assert current['material_records'][0]['after_quantity'] == '8.8'


def test_return_restores_original_batch_and_net_quantity(context):
    data = scenario(context, 'return', stock_batches=((0, '30', 30),))
    material_id = data['order']['materials'][0]['id']
    post_material(data, 'issue', material_id, [{'batch_id': data['batches'][0], 'quantity': '20'}])
    post_material(data, 'supplement', material_id, [{'batch_id': data['batches'][0], 'quantity': '1.2'}])
    response = post_material(data, 'return', material_id, [{'batch_id': data['batches'][0], 'quantity': '0.5'}])
    assert response.status_code == 200, response.text
    current = detail(data)
    row = current['materials'][0]
    assert (row['returned_quantity'], row['net_issued_quantity']) == ('0.5', '20.7')
    assert current['material_records'][0]['change_quantity'] == '0.5'
    assert current['material_records'][0]['after_quantity'] == '9.3'


def test_return_cannot_exceed_batch_net_issued(context):
    data = scenario(context, 'returnlimit', stock_batches=((0, '30', 30),))
    material_id = data['order']['materials'][0]['id']
    post_material(data, 'issue', material_id, [{'batch_id': data['batches'][0], 'quantity': '10'}])
    response = post_material(data, 'return', material_id, [{'batch_id': data['batches'][0], 'quantity': '11'}])
    assert response.status_code == 409
    current = detail(data)
    assert current['materials'][0]['returned_quantity'] == '0'
    assert current['material_records'][0]['after_quantity'] == '20'


def test_insufficient_stock_creates_no_business_records(context):
    data = scenario(context, 'short', stock_batches=((0, '5', 30),))
    material_id = data['order']['materials'][0]['id']
    response = post_material(data, 'issue', material_id, [{'batch_id': data['batches'][0], 'quantity': '8'}])
    assert response.status_code == 409
    current = detail(data)
    assert current['materials'][0]['issued_quantity'] == '0' and current['material_records'] == []
    stock = data['client'].get('/api/material-batches', headers=data['headers']['warehouse'],
                               params={'material_id': data['materials'][0]['id']}).json()['data']['items'][0]
    assert stock['remaining_quantity'] == '5.000000'


def test_fefo_suggests_multiple_batches_in_expiry_order(context):
    data = scenario(context, 'fefo', stock_batches=((0, '20', 60), (0, '5', 20)))
    response = data['client'].get(
        f"/api/production-orders/{data['order']['id']}/materials/{data['materials'][0]['id']}/fefo-suggestion",
        headers=data['headers']['warehouse'], params={'quantity': '10'},
    )
    assert response.status_code == 200, response.text
    suggestion = response.json()['data']
    assert [(row['batch_id'], row['quantity']) for row in suggestion['suggestions']] == [
        (data['batches'][1], '5'), (data['batches'][0], '5'),
    ]


def test_expired_batch_is_not_recommended_or_allowed(context):
    data = scenario(context, 'expired', stock_batches=((0, '5', 30),))
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        batch = session.get(MaterialBatch, data['batches'][0])
        batch.expiry_date = business_today() - timedelta(days=1)
        session.commit()
    suggestion = data['client'].get(
        f"/api/production-orders/{data['order']['id']}/materials/{data['materials'][0]['id']}/fefo-suggestion",
        headers=data['headers']['warehouse'], params={'quantity': '1'},
    ).json()['data']
    assert suggestion['suggestions'] == []
    response = post_material(data, 'issue', data['order']['materials'][0]['id'],
                             [{'batch_id': data['batches'][0], 'quantity': '1'}])
    assert response.status_code == 422


def test_failure_during_ledger_creation_rolls_back_everything(context, monkeypatch):
    data = scenario(context, 'rollback', stock_batches=((0, '30', 30),))
    import app.production_service as service
    monkeypatch.setattr(service, 'create_inventory_transaction', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('forced')))
    response = post_material(data, 'issue', data['order']['materials'][0]['id'],
                             [{'batch_id': data['batches'][0], 'quantity': '20'}])
    assert response.status_code == 500
    current = detail(data)
    assert current['status'] == 'ready' and current['materials'][0]['issued_quantity'] == '0'
    assert current['material_records'] == []
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        assert session.get(MaterialBatch, data['batches'][0]).remaining_quantity == Decimal('30')
        assert session.scalar(select(func.count()).select_from(StockDocument)
                              .where(StockDocument.reference_no == data['order']['order_no'])) == 0


def test_decimal_quantities_are_exact(context):
    data = scenario(context, 'decimal', bom_quantities=('0.1',), stock_batches=((0, '1', 30),))
    row = data['order']['materials'][0]
    assert row['planned_required_quantity'] == '0.1'
    post_material(data, 'issue', row['id'], [{'batch_id': data['batches'][0], 'quantity': '0.1'}])
    post_material(data, 'supplement', row['id'], [{'batch_id': data['batches'][0], 'quantity': '0.2'}])
    assert detail(data)['materials'][0]['net_issued_quantity'] == '0.3'


def test_multi_batch_posting_and_idempotent_replay(context):
    data = scenario(context, 'multi', stock_batches=((0, '8', 20), (0, '15', 60)))
    request_id = uuid4().hex
    payload = [{'batch_id': data['batches'][0], 'quantity': '8'},
               {'batch_id': data['batches'][1], 'quantity': '12'}]
    first = post_material(data, 'issue', data['order']['materials'][0]['id'], payload, request_id=request_id)
    second = post_material(data, 'issue', data['order']['materials'][0]['id'], payload, request_id=request_id)
    assert first.status_code == second.status_code == 200
    assert first.json()['data']['document_no'] == second.json()['data']['document_no']
    current = detail(data)
    assert current['materials'][0]['issued_quantity'] == '20' and len(current['material_records']) == 2


def test_cancel_rules_and_permissions(context):
    clean = scenario(context, 'cancelclean')
    denied = clean['client'].post(f"/api/production-orders/{clean['order']['id']}/cancel",
                                  headers=clean['headers']['warehouse'])
    assert denied.status_code == 403
    cancelled = clean['client'].post(f"/api/production-orders/{clean['order']['id']}/cancel",
                                     headers=clean['headers']['admin'])
    assert cancelled.status_code == 200 and cancelled.json()['data']['status'] == 'cancelled'

    used = scenario(context, 'cancelused', stock_batches=((0, '2', 30),))
    assert post_material(used, 'issue', used['order']['materials'][0]['id'],
                         [{'batch_id': used['batches'][0], 'quantity': '1'}]).status_code == 200
    blocked = used['client'].post(f"/api/production-orders/{used['order']['id']}/cancel",
                                  headers=used['headers']['admin'])
    assert blocked.status_code == 409 and '物料业务' in blocked.json()['message']
    workshop = post_material(used, 'supplement', used['order']['materials'][0]['id'],
                             [{'batch_id': used['batches'][0], 'quantity': '0.1'}], role='workshop')
    assert workshop.status_code == 200  # 第八阶段明确开放车间直接登记补料。


def test_order_list_search_pagination_and_update(context):
    data = scenario(context, 'listupdate')
    order = data['order']
    listed = data['client'].get('/api/production-orders', headers=data['headers']['workshop'],
                                params={'q': order['order_no'], 'status': 'ready', 'page': 1, 'page_size': 10})
    assert listed.status_code == 200
    assert listed.json()['data']['total'] == 1 and listed.json()['data']['items'][0]['id'] == order['id']
    denied = data['client'].put(f"/api/production-orders/{order['id']}", headers=data['headers']['warehouse'],
                                json={'planned_date': '2026-10-01', 'notes': '更新'})
    assert denied.status_code == 403
    updated = data['client'].put(f"/api/production-orders/{order['id']}", headers=data['headers']['admin'],
                                 json={'planned_date': '2026-10-01', 'notes': '更新后的备注'})
    assert updated.status_code == 200
    assert updated.json()['data']['planned_date'] == '2026-10-01'
    assert updated.json()['data']['notes'] == '更新后的备注'
