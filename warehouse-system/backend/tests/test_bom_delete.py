"""BOM 删除真实 MySQL/API 回归：保留版本、历史保护及审计事务。"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BomHeader, BomItem, InventoryTransaction, OperationLog
from test_phase6 import context, scenario  # noqa: F401
from test_phase5 import create_bom, create_material, create_product, material_item

pytestmark = pytest.mark.mysql


def unused(context, suffix, status='draft'):
    client, headers, connection, prefix = context
    product = create_product(client, headers, prefix, suffix=suffix)
    material = create_material(client, headers, prefix, suffix)
    payload = {'product_id': product['id'], 'version': 1, 'status': 'draft' if status == 'archived' else status,
               'items': [material_item(material, '0.000001', 'kg')]}
    response = client.post('/api/boms', headers=headers['admin'], json=payload)
    assert response.status_code == 200, response.text
    if status == 'archived':
        # archived 是既有数据库合法状态，但维护表单只开放草稿/启用/停用。
        with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
            session.get(BomHeader, response.json()['data']['id']).status = 'archived'
            session.commit()
    return product, material, response.json()['data'], payload


@pytest.mark.parametrize('status', ['draft', 'active', 'inactive', 'archived'])
def test_delete_preserves_formula_version_and_excludes_all_reads(context, status):
    client, headers, connection, prefix = context
    product, material, bom, payload = unused(context, 'delete-' + status, status)
    path = f"/api/boms/{bom['id']}"
    with Session(bind=connection) as session:
        before = session.scalar(select(func.count()).select_from(InventoryTransaction))
    response = client.delete(path, headers=headers['admin'])
    assert response.status_code == 200, response.text
    assert client.get(path, headers=headers['admin']).status_code == 404
    assert client.put(path, headers=headers['admin'], json=payload).status_code == 404
    assert client.delete(path, headers=headers['admin']).status_code == 404
    for selected in (None, status, 'archived'):
        params = {'q': product['code']}
        if selected:
            params['status'] = selected
        listed = client.get('/api/boms', headers=headers['admin'], params=params).json()['data']
        assert listed['items'] == [] and listed['total'] == 0
    assert client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1',
    }).status_code == 422
    assert client.post('/api/production-orders', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1',
    }).status_code == 422
    with Session(bind=connection) as session:
        stored = session.get(BomHeader, bom['id'])
        assert stored.deleted_at is not None and stored.status == 'archived'
        assert stored.is_active is False and stored.active_product_id is None
        item = session.scalar(select(BomItem).where(BomItem.bom_id == bom['id']))
        assert item.id == bom['items'][0]['id']
        assert str(item.quantity_per_unit) == '0.000001'
        logs = session.scalars(select(OperationLog).where(
            OperationLog.action == 'delete_bom', OperationLog.entity_id == str(bom['id']),
        )).all()
        assert len(logs) == 1
        assert logs[0].details == {'product_id': product['id'], 'version': 1, 'previous_status': status}
        assert '删除BOM' in logs[0].description
        assert session.scalar(select(func.count()).select_from(InventoryTransaction)) == before
    # 删除保留成品/版本唯一键，但允许其他版本再次启用。
    assert client.post('/api/boms', headers=headers['admin'], json=payload).status_code == 409
    replacement = create_bom(client, headers, product, [material_item(material, '2', 'g')], version=2)
    assert replacement.status_code == 200, replacement.text
    result = client.post('/api/boms/calculate', headers=headers['admin'], json={
        'finished_product_id': product['id'], 'planned_quantity': '1',
    }).json()['data']
    assert result['bom_version'] == 2


@pytest.mark.parametrize('role, expected', [('admin', 200), ('workshop_director', 200),
    ('accountant', 200), ('warehouse', 403), ('workshop', 403), (None, 401)])
def test_delete_permissions(context, role, expected):
    client, headers, connection, _ = context
    _, _, bom, _ = unused(context, 'role-' + str(role))
    response = client.delete(f"/api/boms/{bom['id']}", headers=headers[role] if role else {})
    assert response.status_code == expected, response.text
    with Session(bind=connection) as session:
        assert (session.get(BomHeader, bom['id']).deleted_at is not None) == (expected == 200)


@pytest.mark.parametrize('cancelled', [False, True])
def test_referenced_bom_cannot_be_deleted_even_after_cancellation(context, cancelled):
    client, headers, connection, _ = context
    case = scenario(context, 'protected-' + str(cancelled))
    order_path = f"/api/production-orders/{case['order']['id']}"
    if cancelled:
        assert client.post(order_path + '/cancel', headers=headers['admin']).status_code == 200
    snapshot = client.get(order_path, headers=headers['admin']).json()['data']
    response = client.delete(f"/api/boms/{case['bom']['id']}", headers=headers['admin'])
    assert response.status_code == 409 and '停用' in response.json()['message']
    assert client.get(order_path, headers=headers['admin']).json()['data'] == snapshot
    with Session(bind=connection) as session:
        stored = session.get(BomHeader, case['bom']['id'])
        assert stored.deleted_at is None and stored.status == 'active'
        assert session.scalar(select(func.count()).select_from(OperationLog).where(
            OperationLog.action == 'delete_bom', OperationLog.entity_id == str(stored.id),
        )) == 0


def test_audit_failure_rolls_back_delete(context, monkeypatch):
    from app import bom as bom_module
    client, headers, connection, _ = context
    product, _, bom, _ = unused(context, 'rollback', 'active')
    original_audit = bom_module.audit

    def broken_audit(session, *args, **kwargs):
        # 先把软删除写到数据库，再注入故障，证明整个请求事务回滚。
        session.flush()
        original_audit(session, *args, **kwargs)
        session.flush()
        raise RuntimeError('injected audit failure')

    monkeypatch.setattr(bom_module, 'audit', broken_audit)
    assert client.delete(f"/api/boms/{bom['id']}", headers=headers['admin']).status_code == 500
    with Session(bind=connection) as session:
        stored = session.get(BomHeader, bom['id'])
        assert stored.deleted_at is None and stored.status == 'active'
        assert stored.active_product_id == product['id']
        assert session.scalar(select(func.count()).select_from(OperationLog).where(
            OperationLog.action == 'delete_bom', OperationLog.entity_id == str(stored.id),
        )) == 0
    assert client.get(f"/api/boms/{bom['id']}", headers=headers['admin']).status_code == 200


def test_delete_missing_bom(context):
    client, headers, _, _ = context
    assert client.delete('/api/boms/2147483647', headers=headers['admin']).status_code == 404
