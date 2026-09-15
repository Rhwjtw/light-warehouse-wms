from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import engine, get_session
from app.main import app
from app.models import BomHeader, BomItem, InventoryTransaction, MaterialBatch, Role, User

pytestmark = pytest.mark.mysql
PASSWORD = 'Test-Password-For-Tests-Only!'


@pytest.fixture(scope='module')
def context():
    connection = engine.connect()
    outer = connection.begin()
    prefix = 'bom_' + uuid4().hex[:10]

    def db():
        with Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
            with session.begin():
                yield session

    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        users = {}
        for code in ('admin', 'warehouse'):
            role = session.scalar(select(Role).where(Role.code == code))
            user = User(username=f'{prefix}_{code}', display_name=code, role_id=role.id, password_hash=hash_password(PASSWORD))
            session.add(user); session.flush()
            users[code] = user.username
        session.commit()
    app.dependency_overrides[get_session] = db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = {}
            for code, username in users.items():
                response = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD})
                headers[code] = {'Authorization': 'Bearer ' + response.json()['data']['access_token']}
            yield client, headers, connection, prefix
    finally:
        app.dependency_overrides.clear()
        outer.rollback()
        connection.close()


def create_product(client, headers, prefix, *, unit='盒', suffix=''):
    response = client.post('/api/products', headers=headers['admin'], json={
        'code': f'{prefix}p{suffix}', 'name': f'测试面霜{suffix}', 'unit': unit,
    })
    assert response.status_code == 200, response.text
    return response.json()['data']


def create_material(client, headers, prefix, suffix, *, unit='kg'):
    response = client.post('/api/materials', headers=headers['admin'], json={
        'code': f'{prefix}m{suffix}', 'name': f'原料{suffix}', 'unit': unit,
    })
    assert response.status_code == 200, response.text
    return response.json()['data']


def create_bom(client, headers, product, items, *, status='active', version=1):
    response = client.post('/api/boms', headers=headers['admin'], json={
        'product_id': product['id'], 'version': version, 'status': status, 'items': items,
    })
    return response


def material_item(material, quantity, unit, sort_order=0):
    return {'material_id': material['id'], 'quantity_per_unit': quantity, 'unit': unit, 'sort_order': sort_order}


def test_create_bom_with_multiple_items(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='create')
    materials = [create_material(client, headers, prefix, name) for name in ('Acreate', 'Bcreate', 'Ccreate')]
    result = create_bom(client, headers, product, [
        material_item(materials[0], '20', 'g', 1),
        material_item(materials[1], '5', 'g', 2),
        material_item(materials[2], '2', 'g', 3),
    ])
    assert result.status_code == 200, result.text
    data = result.json()['data']
    assert data['product_unit'] == '盒' and data['version'] == 1 and data['status'] == 'active'
    assert [item['quantity_per_unit'] for item in data['items']] == ['20', '5', '2']
    assert client.get(f"/api/boms/{data['id']}", headers=headers['warehouse']).status_code == 200


def test_calculate_1000_boxes_exactly(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='calc')
    materials = [create_material(client, headers, prefix, name) for name in ('Acalc', 'Bcalc', 'Ccalc')]
    create_bom(client, headers, product, [material_item(material, quantity, 'g', index)
               for index, (material, quantity) in enumerate(zip(materials, ('20', '5', '2')), 1)])
    response = client.post('/api/boms/calculate', headers=headers['warehouse'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1000',
    })
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['finished_product_unit'] == '盒' and data['planned_quantity'] == '1000'
    assert [(row['total_quantity'], row['total_unit']) for row in data['requirements']] == [('20000', 'g'), ('5000', 'g'), ('2000', 'g')]
    assert [(row['suggested_quantity'], row['suggested_unit']) for row in data['requirements']] == [('20', 'kg'), ('5', 'kg'), ('2', 'kg')]


def test_decimal_precision_is_exact(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='decimal')
    material = create_material(client, headers, prefix, 'decimal', unit='g')
    create_bom(client, headers, product, [material_item(material, '0.1', 'g')])
    row = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '3',
    }).json()['data']['requirements'][0]
    assert row['total_quantity'] == '0.3'
    assert '00000000000000004' not in row['total_quantity']


def test_duplicate_material_is_rejected(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='duplicate')
    material = create_material(client, headers, prefix, 'duplicate')
    result = create_bom(client, headers, product, [material_item(material, '1', 'g'), material_item(material, '2', 'g')])
    assert result.status_code == 422
    assert '重复' in result.json()['message']


@pytest.mark.parametrize('quantity', ['0', '-1'])
def test_nonpositive_item_quantity_is_rejected(context, quantity):
    client, headers, _, prefix = context
    suffix = 'zero' if quantity == '0' else 'negative'
    product = create_product(client, headers, prefix, suffix=suffix)
    material = create_material(client, headers, prefix, suffix)
    result = create_bom(client, headers, product, [material_item(material, quantity, 'g')])
    assert result.status_code == 422


def test_product_without_active_bom_has_business_error(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='nobom')
    response = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1000',
    })
    assert response.status_code == 422
    assert response.json()['message'] == '该成品尚未配置有效BOM。'


@pytest.mark.parametrize('payload', [
    {'planned_quantity': '0'}, {'planned_quantity': '-1'}, {},
])
def test_invalid_planned_quantity_is_rejected(context, payload):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='plan' + uuid4().hex[:5])
    response = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], **payload,
    })
    assert response.status_code == 422


def test_grams_to_kilograms(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='gkg')
    material = create_material(client, headers, prefix, 'gkg', unit='kg')
    create_bom(client, headers, product, [material_item(material, '20', 'g')])
    row = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1000',
    }).json()['data']['requirements'][0]
    assert (row['suggested_quantity'], row['suggested_unit']) == ('20', 'kg')


def test_millilitres_to_litres(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='ml')
    material = create_material(client, headers, prefix, 'ml', unit='L')
    create_bom(client, headers, product, [material_item(material, '5', 'mL')])
    row = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1000',
    }).json()['data']['requirements'][0]
    assert (row['total_quantity'], row['total_unit']) == ('5000', 'mL')
    assert (row['suggested_quantity'], row['suggested_unit']) == ('5', 'L')


def test_incompatible_units_are_rejected(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='incompatible')
    material = create_material(client, headers, prefix, 'incompatible', unit='L')
    result = create_bom(client, headers, product, [material_item(material, '20', 'g')])
    assert result.status_code == 422
    assert '不兼容' in result.json()['message']


def test_corrupted_bom_unit_is_reported_during_calculation(context):
    client, headers, connection, prefix = context
    product = create_product(client, headers, prefix, suffix='corrupt')
    material = create_material(client, headers, prefix, 'corrupt', unit='kg')
    created = create_bom(client, headers, product, [material_item(material, '20', 'g')]).json()['data']
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        item = session.scalar(select(BomItem).where(BomItem.bom_id == created['id']))
        item.unit = 'L'
        session.commit()
    response = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1000',
    })
    assert response.status_code == 422
    assert '不兼容' in response.json()['message']


def test_calculation_never_changes_inventory(context):
    client, headers, connection, prefix = context
    product = create_product(client, headers, prefix, suffix='stock')
    material = create_material(client, headers, prefix, 'stock')
    create_bom(client, headers, product, [material_item(material, '20', 'g')])
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        before_batches = session.scalar(select(func.coalesce(func.sum(MaterialBatch.remaining_quantity), 0)))
        before_transactions = session.scalar(select(func.count()).select_from(InventoryTransaction))
    for _ in range(3):
        assert client.post('/api/boms/calculate', headers=headers['warehouse'], json={
            'finished_product_id': product['id'], 'planned_quantity': '1000',
        }).status_code == 200
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        assert session.scalar(select(func.coalesce(func.sum(MaterialBatch.remaining_quantity), 0))) == before_batches
        assert session.scalar(select(func.count()).select_from(InventoryTransaction)) == before_transactions


def test_only_one_active_bom_and_status_update(context):
    client, headers, connection, prefix = context
    product = create_product(client, headers, prefix, suffix='version')
    material = create_material(client, headers, prefix, 'version')
    first = create_bom(client, headers, product, [material_item(material, '1', 'g')], version=1)
    second = create_bom(client, headers, product, [material_item(material, '2', 'g')], version=2)
    assert second.status_code == 409
    payload = first.json()['data']
    update = {'product_id': product['id'], 'version': 1, 'status': 'inactive', 'items': [
        material_item(material, '1', 'g')], 'notes': None}
    assert client.put(f"/api/boms/{payload['id']}", headers=headers['admin'], json=update).status_code == 200
    assert create_bom(client, headers, product, [material_item(material, '2', 'g')], version=2).status_code == 200
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        active = session.scalars(select(BomHeader).where(BomHeader.product_id == product['id'], BomHeader.status == 'active')).all()
        assert len(active) == 1


def test_bom_write_permissions_and_search(context):
    client, headers, _, prefix = context
    product = create_product(client, headers, prefix, suffix='search')
    material = create_material(client, headers, prefix, 'search')
    payload = {'product_id': product['id'], 'version': 1, 'status': 'draft', 'items': [material_item(material, '1', 'g')]}
    assert client.post('/api/boms', headers=headers['warehouse'], json=payload).status_code == 403
    assert client.post('/api/boms', headers=headers['admin'], json=payload).status_code == 200
    listed = client.get('/api/boms', headers=headers['warehouse'], params={'q': '测试面霜search'}).json()['data']
    assert listed['total'] == 1 and listed['items'][0]['product_unit'] == '盒'
