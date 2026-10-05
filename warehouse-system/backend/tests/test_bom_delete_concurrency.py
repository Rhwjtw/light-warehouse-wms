"""独立连接验证删除与建单共享 BOM 锁，不产生指向已删除配方的订单。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import BomHeader, OperationLog, ProductionOrder
from test_bom_delete import unused
from test_phase7_concurrency import committed_production  # noqa: F401

pytestmark = pytest.mark.mysql


def test_concurrent_bom_delete_only_audits_once(committed_production):
    client, headers, _, _ = committed_production
    _, _, bom, _ = unused(committed_production, 'delete-race', 'active')
    barrier = Barrier(2)

    def remove(_):
        barrier.wait(timeout=10)
        return client.delete(f"/api/boms/{bom['id']}", headers=headers['admin'])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(remove, range(2)))
    assert sorted(row.status_code for row in results) == [200, 404]
    with SessionLocal() as session:
        assert session.get(BomHeader, bom['id']).deleted_at is not None
        assert session.scalar(select(func.count()).select_from(OperationLog).where(
            OperationLog.action == 'delete_bom', OperationLog.entity_id == str(bom['id']),
        )) == 1


def test_concurrent_bom_delete_and_production_creation(committed_production):
    client, headers, _, _ = committed_production
    product, _, bom, _ = unused(committed_production, 'order-race', 'active')
    barrier = Barrier(2)

    def action(kind):
        barrier.wait(timeout=10)
        if kind == 'delete':
            return client.delete(f"/api/boms/{bom['id']}", headers=headers['admin'])
        return client.post('/api/production-orders', headers=headers['admin'], json={
            'finished_product_id': product['id'], 'planned_quantity': '1',
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted, created = list(pool.map(action, ['delete', 'create']))
    assert (deleted.status_code, created.status_code) in ((200, 422), (409, 200))
    with SessionLocal() as session:
        stored = session.get(BomHeader, bom['id'])
        orders = session.scalars(select(ProductionOrder).where(ProductionOrder.bom_id == bom['id'])).all()
        assert len(orders) == (1 if created.status_code == 200 else 0)
        assert (stored.deleted_at is not None) == (deleted.status_code == 200)
        if orders:
            assert orders[0].bom_version == 1
