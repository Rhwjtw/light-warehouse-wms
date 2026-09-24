"""Custom units and five-role read/write boundaries on isolated MySQL."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.stock_service import business_today
from test_phase2 import context

pytestmark = pytest.mark.mysql


def post(client, headers, role, path, payload):
    return client.post('/api/' + path, headers=headers[role], json=payload)


def test_custom_units_flow_and_existing_units(context):
    client, headers, _, _, prefix = context
    material = post(client, headers, 'accountant', 'materials', {
        'code': prefix + 'roll', 'name': '自定义卷材', 'unit': '  卷  ',
    })
    product = post(client, headers, 'accountant', 'products', {
        'code': prefix + 'board', 'name': '自定义板材', 'unit': '板',
    })
    assert material.status_code == product.status_code == 200
    material, product = material.json()['data'], product.json()['data']
    assert material['unit'] == '卷' and product['unit'] == '板'
    assert {'卷', '板', 'kg', 'g', 'L', 'mL', '盒', '个', '袋', '箱'} <= {
        row['code'] for row in client.get('/api/units', headers=headers['workshop']).json()['data']}
    for resource, row in [('materials', material), ('products', product)]:
        detail = client.get(f"/api/{resource}/{row['id']}", headers=headers['workshop']).json()['data']
        assert detail['unit'] == row['unit']
        payload = {key: row[key] for key in ('code', 'name', 'unit', 'is_active')}
        payload['name'] += '编辑'
        updated = client.put(f"/api/{resource}/{row['id']}", headers=headers['accountant'], json=payload)
        assert updated.status_code == 200 and updated.json()['data']['unit'] == row['unit']
    for bad in (' ', '\t\n', 'X' * 17):
        assert post(client, headers, 'accountant', 'materials', {
            'code': prefix + 'bad', 'name': '无效单位', 'unit': bad,
        }).status_code == 422

    supplier = post(client, headers, 'warehouse', 'suppliers', {'code': prefix+'s', 'name': '测试供应商'}).json()['data']
    location = post(client, headers, 'warehouse', 'locations', {'code': prefix+'l', 'name': '测试库位'}).json()['data']
    receipt = post(client, headers, 'warehouse', 'material-receipts', {
        'request_id': uuid4().hex, 'material_id': material['id'], 'batch_no': prefix+'batch',
        'quantity': '30', 'unit': '卷', 'production_date': str(business_today()-timedelta(days=1)),
        'expiry_date': str(business_today()+timedelta(days=90)),
        'supplier_id': supplier['id'], 'location_id': location['id'],
    })
    assert receipt.status_code == 200, receipt.text
    batch_id = receipt.json()['data']['batch_id']
    assert receipt.json()['data']['unit'] == '卷'
    assert client.get('/api/material-stock', headers=headers['accountant'], params={'q': material['code']}).json()['data']['items'][0]['unit'] == '卷'
    assert client.get('/api/material-batches', headers=headers['accountant'], params={'material_id': material['id']}).json()['data']['items'][0]['unit'] == '卷'
    bom = post(client, headers, 'accountant', 'boms', {
        'product_id': product['id'], 'version': 1, 'status': 'active',
        'items': [{'material_id': material['id'], 'quantity_per_unit': '2', 'unit': '卷'}],
    })
    assert bom.status_code == 200, bom.text
    assert bom.json()['data']['product_unit'] == '板'
    preview = post(client, headers, 'workshop', 'boms/calculate', {
        'finished_product_id': product['id'], 'planned_quantity': '10',
    })
    assert preview.status_code == 200 and preview.json()['data']['requirements'][0]['suggested_unit'] == '卷'
    order = post(client, headers, 'accountant', 'production-orders', {
        'finished_product_id': product['id'], 'planned_quantity': '10',
    })
    assert order.status_code == 200, order.text
    order = order.json()['data']
    assert order['finished_product_unit'] == '板' and order['materials'][0]['base_unit'] == '卷'
    path = f"production-orders/{order['id']}"
    def material_action(kind, quantity):
        return post(client, headers, 'workshop', path+'/'+kind, {
            'request_id': uuid4().hex, 'order_material_id': order['materials'][0]['id'],
            'unit': '卷', 'allocations': [{'batch_id': batch_id, 'quantity': quantity}],
        })
    for kind, quantity in [('issue', '20'), ('supplement', '1'), ('return', '1')]:
        result = material_action(kind, quantity)
        assert result.status_code == 200, result.text
    assert client.put('/api/'+path+'/actual-quantity', headers=headers['workshop_director'],
                      json={'actual_quantity': '10'}).status_code == 200
    finished = post(client, headers, 'workshop_director', path+'/complete', {'actual_quantity': '10'})
    assert finished.status_code == 200, finished.text
    assert finished.json()['data']['materials'][0]['loss_quantity'] == '0'
    stock = client.get(f"/api/finished-stock/{product['id']}", headers=headers['workshop']).json()['data']
    assert stock['unit'] == '板' and stock['quantity'] == '10.000000'
    assert client.get('/api/finished-transactions', headers=headers['workshop'],
                      params={'product_id': product['id']}).json()['data']['items'][0]['unit'] == '板'
    assert client.get('/api/losses', headers=headers['workshop'],
                      params={'material_id': material['id']}).json()['data']['items'][0]['base_unit'] == '卷'
    assert client.get('/api/material-transactions', headers=headers['workshop'],
                      params={'material_id': material['id']}).json()['data']['items'][0]['unit'] == '卷'
    outbound = post(client, headers, 'warehouse', 'material-outbounds', {
        'request_id': uuid4().hex, 'material_id': material['id'], 'batch_id': batch_id,
        'quantity': '1', 'unit': '卷',
    })
    assert outbound.status_code == 200 and outbound.json()['data']['unit'] == '卷'
    finished_outbound = post(client, headers, 'warehouse', 'finished-outbounds', {
        'request_id': uuid4().hex, 'product_id': product['id'], 'quantity': '2',
        'unit': '板', 'business_date': str(business_today()),
    })
    assert finished_outbound.status_code == 200 and finished_outbound.json()['data']['unit'] == '板'
    assert client.get(f"/api/finished-stock/{product['id']}", headers=headers['accountant']).json()['data']['quantity'] == '8.000000'


def test_five_roles_direct_api_boundaries(context):
    client, headers, users, _, prefix = context
    for role in headers:
        for path in ('materials', 'material-stock', 'products', 'finished-stock', 'boms',
                     'production-orders', 'users', 'roles', 'settings/expiry', 'operation-logs'):
            assert client.get('/api/'+path, headers=headers[role]).status_code == 200, (role, path)
    material_payload = {'code': prefix+'access', 'name': '权限原料', 'unit': 'kg'}
    assert post(client, headers, 'warehouse', 'materials', material_payload).status_code == 200
    assert post(client, headers, 'accountant', 'materials', {**material_payload, 'code': prefix+'account'}).status_code == 200
    for role in ('workshop_director', 'workshop'):
        assert post(client, headers, role, 'materials', {**material_payload, 'code': prefix+role}).status_code == 403
    material = client.get('/api/materials', headers=headers['admin'], params={'q': prefix+'access'}).json()['data']['items'][0]
    assert client.put(f"/api/materials/{material['id']}", headers=headers['accountant'],
                      json={key: material[key] for key in ('code','name','unit','is_active')}).status_code == 200
    product = post(client, headers, 'accountant', 'products', {'code': prefix+'p', 'name': '权限成品', 'unit': '盒'}).json()['data']
    supplier = post(client, headers, 'accountant', 'suppliers', {'code': prefix+'s', 'name': '权限供应商'}).json()['data']
    location = post(client, headers, 'warehouse', 'locations', {'code': prefix+'loc', 'name': '权限库位'}).json()['data']
    receipt_payload = {
        'request_id': uuid4().hex, 'material_id': material['id'], 'batch_no': prefix+'batch',
        'quantity': '30', 'unit': 'kg', 'production_date': str(business_today()-timedelta(days=1)),
        'expiry_date': str(business_today()+timedelta(days=90)),
        'supplier_id': supplier['id'], 'location_id': location['id'],
    }
    for role in ('accountant', 'workshop_director', 'workshop'):
        assert post(client, headers, role, 'material-receipts', receipt_payload).status_code == 403
    receipt = post(client, headers, 'warehouse', 'material-receipts', receipt_payload)
    assert receipt.status_code == 200
    finished_payload = {'request_id': uuid4().hex, 'product_id': product['id'], 'quantity': '1',
                        'unit': '盒', 'business_date': str(business_today())}
    for role in ('accountant', 'workshop_director', 'workshop'):
        assert post(client, headers, role, 'finished-receipts', finished_payload).status_code == 403
    assert post(client, headers, 'warehouse', 'finished-receipts', finished_payload).status_code == 200
    bom_payload = {'product_id': product['id'], 'version': 1, 'status': 'active',
                   'items': [{'material_id': material['id'], 'quantity_per_unit': '1', 'unit': 'kg'}]}
    for role in ('warehouse', 'workshop'):
        assert post(client, headers, role, 'boms', bom_payload).status_code == 403
    assert post(client, headers, 'accountant', 'boms', bom_payload).status_code == 200
    order_payload = {'finished_product_id': product['id'], 'planned_quantity': '10'}
    for role in ('warehouse', 'workshop'):
        assert post(client, headers, role, 'production-orders', order_payload).status_code == 403
    order = post(client, headers, 'accountant', 'production-orders', order_payload)
    assert order.status_code == 200
    order = order.json()['data']
    path = f"production-orders/{order['id']}"
    posting = {'request_id': uuid4().hex, 'order_material_id': order['materials'][0]['id'],
               'unit': 'kg', 'allocations': [{'batch_id': receipt.json()['data']['batch_id'], 'quantity': '10'}]}
    for role in ('warehouse', 'accountant'):
        assert post(client, headers, role, path+'/issue', posting).status_code == 403
    assert post(client, headers, 'workshop', path+'/issue', posting).status_code == 200
    assert client.put('/api/'+path+'/actual-quantity', headers=headers['accountant'],
                      json={'actual_quantity':'10'}).status_code == 403
    assert client.put('/api/'+path+'/actual-quantity', headers=headers['workshop'],
                      json={'actual_quantity':'10'}).status_code == 403
    assert client.put('/api/'+path+'/actual-quantity', headers=headers['workshop_director'],
                      json={'actual_quantity':'10'}).status_code == 200
    assert post(client, headers, 'workshop', path+'/settlement-preview', {'actual_quantity':'10'}).status_code == 200
    for role in ('warehouse', 'accountant', 'workshop'):
        assert post(client, headers, role, path+'/complete', {'actual_quantity':'10'}).status_code == 403
    assert post(client, headers, 'workshop_director', path+'/complete', {'actual_quantity':'10'}).status_code == 200
    assert post(client, headers, 'accountant', 'locations', {'code': prefix+'l', 'name':'越权'}).status_code == 403
    assert client.put('/api/settings/expiry', headers=headers['accountant'],
                      json={'urgent_expiry_days':30, 'warning_expiry_days':90}).status_code == 403
    assert client.put(f"/api/users/{users['accountant']['id']}", headers=headers['accountant'],
                      json={'display_name':'提权','role_id':users['admin']['role_id']}).status_code == 403
