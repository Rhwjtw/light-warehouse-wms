from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import catalog
from app.auth import hash_password
from app.database import engine, get_session
from app.main import app
from app.models import BomHeader, BomItem, OperationLog, RawMaterial, Role, User, utcnow

pytestmark = pytest.mark.mysql
PASSWORD = 'Test-Password-For-Tests-Only!'


@pytest.fixture
def context():
    """真实 MySQL + 外层回滚；每个 API 仍有独立事务/savepoint，不污染演示资料。"""
    connection = engine.connect()
    outer = connection.begin()
    prefix = 'test_' + uuid4().hex[:10]
    identities = {}
    def db():
        with Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
            with session.begin():
                yield session
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        for code in ('admin', 'warehouse', 'workshop'):
            role = session.scalar(select(Role).where(Role.code == code))
            user = User(username=prefix+'_'+code, display_name=code, role_id=role.id, password_hash=hash_password(PASSWORD))
            session.add(user); session.flush()
            identities[code] = {'id': user.id, 'username': user.username, 'role_id': role.id}
        session.commit()
    app.dependency_overrides[get_session] = db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = {}
            for code, user in identities.items():
                response = client.post('/api/auth/login', json={'username': user['username'], 'password': PASSWORD})
                assert response.status_code == 200, response.text
                headers[code] = {'Authorization': 'Bearer ' + response.json()['data']['access_token']}
            yield client, headers, identities, connection, prefix
    finally:
        app.dependency_overrides.clear()
        outer.rollback(); connection.close()


def test_authentication_and_secret_redaction(context):
    client, headers, users, _, _ = context
    assert client.get('/api/materials').status_code == 401
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer nonsense'}).status_code == 401
    response = client.get('/api/auth/me', headers=headers['admin'])
    assert response.status_code == 200
    assert response.json()['data']['role'] == 'admin'
    response = client.get('/api/users', headers=headers['admin'])
    assert response.status_code == 200
    for key in ('password_hash', 'password', 'token_version', 'locked_until'):
        assert f'"{key}"' not in response.text
    assert PASSWORD not in response.text


@pytest.mark.parametrize('resource,extra', [('materials', {'unit': 'kg', 'min_stock': '1.123456'}),
    ('products', {}), ('suppliers', {'phone': '13800000000', 'contact': '测试'}), ('locations', {})])
def test_catalog_crud_search_pagination_and_soft_delete(context, resource, extra):
    client, headers, _, _, prefix = context
    h = headers['admin']
    payload = {'code': prefix, 'name': '  专用测试档案  ', **extra}
    created = client.post('/api/'+resource, headers=h, json=payload)
    assert created.status_code == 200, created.text
    data = created.json()['data']; identity = data['id']
    assert data['name'] == '专用测试档案'
    if resource == 'products': assert data['unit'] == '盒'
    if resource == 'materials': assert data['min_stock'] == '1.123456'
    assert client.post('/api/'+resource, headers=h, json=payload).status_code == 409
    listed = client.get('/api/'+resource, headers=h, params={'q': prefix, 'page_size': 1}).json()['data']
    assert listed['total'] == 1 and len(listed['items']) == 1
    assert client.get('/api/'+resource, headers=h, params={'q': prefix, 'page': 2, 'page_size': 1}).json()['data']['items'] == []
    assert client.get('/api/'+resource, headers=h, params={'q': '%'}).json()['data']['total'] == 0
    assert client.get('/api/'+resource, headers=h, params={'page': 0}).status_code == 422
    assert client.get('/api/'+resource, headers=h, params={'page_size': 101}).status_code == 422
    payload.update({'name': '已编辑', 'is_active': False})
    assert client.put(f'/api/{resource}/{identity}', headers=h, json=payload).status_code == 200
    assert client.get('/api/'+resource, headers=h, params={'q': prefix, 'is_active': True}).json()['data']['total'] == 0
    assert client.get(f'/api/{resource}/{identity}', headers=h).json()['data']['name'] == '已编辑'
    assert client.delete(f'/api/{resource}/{identity}', headers=h).status_code == 200
    assert client.get(f'/api/{resource}/{identity}', headers=h).status_code == 404
    assert client.post('/api/'+resource, headers=h, json=payload).status_code == 409


def test_invalid_quantities_units_and_inventory_injection(context):
    client, headers, _, _, prefix = context
    base = {'code': prefix, 'name': '校验', 'unit': 'kg'}
    for change in ({'min_stock': '-1'}, {'min_stock': '1.1234567'}, {'min_stock': 'NaN'}, {'min_stock': 1.2},
                   {'min_stock': True}, {'unit': '吨'}, {'unit': '盒'}, {'remaining_quantity': '100'}, {'name': '   '}):
        response = client.post('/api/materials', headers=headers['admin'], json={**base, **change})
        assert response.status_code == 422, response.text
    assert client.post('/api/products', headers=headers['admin'], json={**base, 'unit': 'kg'}).status_code == 422


@pytest.mark.parametrize('role', ['warehouse', 'workshop'])
def test_non_admin_cannot_write_or_manage_users(context, role):
    client, headers, users, _, prefix = context
    for resource in ('materials', 'products', 'suppliers', 'locations'):
        assert client.get('/api/'+resource, headers=headers[role]).status_code == 200
        payload = {'code': prefix, 'name': '越权', **({'unit': 'kg'} if resource == 'materials' else {})}
        assert client.post('/api/'+resource, headers=headers[role], json=payload).status_code == 403
        assert client.put('/api/'+resource+'/1', headers=headers[role], json=payload).status_code == 403
        assert client.delete('/api/'+resource+'/1', headers=headers[role]).status_code == 403
    assert client.get('/api/users', headers=headers[role]).status_code == 403
    assert client.get('/api/roles', headers=headers[role]).status_code == 403
    assert client.post(f"/api/users/{users['admin']['id']}/password", headers=headers[role], json={'new_password': PASSWORD}).status_code == 403


def test_users_create_edit_revoke_reset_and_delete(context):
    client, headers, users, _, prefix = context
    h = headers['admin']
    payload = {'username': prefix+'_new', 'display_name': '新员工', 'password': PASSWORD, 'role_id': users['warehouse']['role_id']}
    result = client.post('/api/users', headers=h, json=payload)
    assert result.status_code == 200, result.text
    identity = result.json()['data']['id']
    assert client.post('/api/users', headers=h, json=payload).status_code == 409
    assert client.get(f'/api/users/{identity}', headers=h).json()['data']['display_name'] == '新员工'
    assert client.delete(f'/api/users/{identity}', headers=h).status_code == 200
    assert client.post('/api/auth/login', json={'username': payload['username'], 'password': PASSWORD}).status_code == 401
    target = users['warehouse']
    update = {'display_name': '改名', 'role_id': target['role_id'], 'is_active': False}
    assert client.put(f"/api/users/{target['id']}", headers=h, json=update).status_code == 200
    assert client.get('/api/auth/me', headers=headers['warehouse']).status_code == 401
    update['is_active'] = True
    assert client.put(f"/api/users/{target['id']}", headers=h, json=update).status_code == 200
    assert client.get('/api/auth/me', headers=headers['warehouse']).status_code == 401
    new_password = 'New-Secret-Password!'
    assert client.post(f"/api/users/{target['id']}/password", headers=h, json={'new_password': new_password}).status_code == 200
    assert client.post('/api/auth/login', json={'username': target['username'], 'password': PASSWORD}).status_code == 401
    assert client.post('/api/auth/login', json={'username': target['username'], 'password': new_password}).status_code == 200
    assert client.delete(f"/api/users/{target['id']}", headers=h).status_code == 409
    assert client.put(f"/api/users/{users['admin']['id']}", headers=h, json=update).status_code == 409


def test_password_change_logout_and_failed_login_lock(context):
    client, headers, users, connection, _ = context
    who = users['workshop']
    response = client.post('/api/auth/password', headers=headers['workshop'], json={'current_password': PASSWORD, 'new_password': 'Updated-Test-Password!'})
    assert response.status_code == 200
    assert client.get('/api/auth/me', headers=headers['workshop']).status_code == 401
    fresh = client.post('/api/auth/login', json={'username': who['username'], 'password': 'Updated-Test-Password!'})
    h = {'Authorization': 'Bearer '+fresh.json()['data']['access_token']}
    assert client.post('/api/auth/logout', headers=h).status_code == 200
    assert client.get('/api/auth/me', headers=h).status_code == 401
    for _ in range(5):
        assert client.post('/api/auth/login', json={'username': who['username'], 'password': 'wrong'}).status_code == 401
    assert client.post('/api/auth/login', json={'username': who['username'], 'password': 'Updated-Test-Password!'}).status_code == 401
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        row = session.get(User, who['id']); assert row.failed_login_count == 5
        row.locked_until = utcnow() - timedelta(seconds=1); session.commit()
    assert client.post('/api/auth/login', json={'username': who['username'], 'password': 'Updated-Test-Password!'}).status_code == 200


def test_referenced_material_unit_and_delete_are_blocked(context):
    client, headers, _, connection, prefix = context
    h = headers['admin']
    material = client.post('/api/materials', headers=h, json={'code': prefix, 'name': '配方原料', 'unit': 'kg'}).json()['data']
    product = client.post('/api/products', headers=h, json={'code': prefix, 'name': '配方成品'}).json()['data']
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        bom = BomHeader(product_id=product['id'], version=1, status='draft', output_unit='盒')
        session.add(bom); session.flush()
        session.add(BomItem(bom_id=bom.id, material_id=material['id'], quantity_per_unit=Decimal('20'), unit='g')); session.commit()
    assert client.put(f"/api/materials/{material['id']}", headers=h, json={'code': prefix, 'name': '配方原料', 'unit': 'g'}).status_code == 409
    assert client.delete(f"/api/materials/{material['id']}", headers=h).status_code == 409
    assert client.put(f"/api/materials/{material['id']}", headers=h, json={'code': prefix, 'name': '配方原料', 'unit': 'kg', 'is_active': False}).status_code == 200


def test_record_and_log_rollback_together_on_failure(context, monkeypatch):
    client, headers, _, connection, prefix = context
    def fail_after_log(session, *args, **kwargs):
        session.add(OperationLog(action='simulated_failure', entity_type='test', entity_id=prefix))
        session.flush()
        raise RuntimeError('simulated failure')
    monkeypatch.setattr(catalog, 'audit', fail_after_log)
    response = client.post('/api/materials', headers=headers['admin'], json={'code': prefix, 'name': '应回滚', 'unit': 'kg'})
    assert response.status_code == 500
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        assert session.scalar(select(RawMaterial).where(RawMaterial.code == prefix)) is None
        assert session.scalar(select(func.count()).select_from(OperationLog).where(OperationLog.entity_id == prefix)) == 0
