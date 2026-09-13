"""成品独立连接测试；随机专属数据真实提交后精确清理。"""
from datetime import timedelta
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import Session

from test_phase3_concurrency import race
from app.config import get_settings
from app.database import SessionLocal
from app.models import (User, Role, FinishedProduct, FinishedBatch, FinishedReceipt, FinishedOutbound,
                        StockDocument, InventoryTransaction, OperationLog, utcnow)
from app.stock_service import business_today


@pytest.fixture
def committed_finished_inventory():
    settings = get_settings()
    if settings.app_env != 'development' or settings.mysql_database != 'warehouse_system':
        pytest.skip('真实提交测试仅允许本地 development warehouse_system')
    prefix = 'test_finished_' + uuid4().hex[:12]
    with SessionLocal.begin() as session:
        user = User(username=prefix, display_name=prefix, password_hash='!unusable-test-account',
                    role_id=session.scalar(select(Role.id).where(Role.code == 'warehouse')))
        product = FinishedProduct(code=prefix, name=prefix, unit='盒')
        session.add_all([user, product])
        session.flush()
        user_id, product_id = user.id, product.id
    now = utcnow()
    token = jwt.encode({'sub': str(user_id), 'ver': 0, 'iat': now, 'exp': now + timedelta(minutes=30),
                        'iss': 'warehouse-api', 'aud': 'warehouse'}, settings.auth_secret.get_secret_value(), algorithm='HS256')
    payload = dict(product_id=product_id, quantity='1000', unit='盒', business_date=str(business_today()),
                   business_type='normal', request_id=uuid4().hex)
    try:
        yield {'Authorization': 'Bearer '+token}, payload, {'user':user_id,'product':product_id,'prefix':prefix}
    finally:
        cleanup = create_engine(settings.database_url.set(username=settings.mysql_migration_user,
                                password=settings.mysql_migration_password.get_secret_value()))
        with Session(cleanup) as session, session.begin():
            assert session.get(User,user_id).username == prefix
            assert session.get(FinishedProduct,product_id).code == prefix
            docs = select(StockDocument.id).where(StockDocument.operator_id == user_id)
            session.execute(delete(InventoryTransaction).where(InventoryTransaction.operator_id == user_id))
            session.execute(delete(FinishedReceipt).where(FinishedReceipt.document_id.in_(docs)))
            session.execute(delete(FinishedOutbound).where(FinishedOutbound.document_id.in_(docs)))
            session.execute(delete(OperationLog).where(OperationLog.user_id == user_id))
            session.execute(delete(StockDocument).where(StockDocument.operator_id == user_id))
            session.execute(delete(FinishedBatch).where(FinishedBatch.product_id == product_id))
            session.execute(delete(FinishedProduct).where(FinishedProduct.id == product_id))
            session.execute(delete(User).where(User.id == user_id))
        cleanup.dispose()


def test_concurrent_finished_postings(committed_finished_inventory):
    h,p,ids = committed_finished_inventory
    def quantity():
        with SessionLocal() as session:
            batches = list(session.scalars(select(FinishedBatch).where(FinishedBatch.product_id == ids['product'])))
            assert len(batches) == 1
            return batches[0].remaining_quantity
    results = race('/api/finished-receipts', [p,p], h)
    assert [r.status_code for r in results] == [200,200]
    assert results[0].json()['data'] == results[1].json()['data']
    assert quantity() == 1000
    results = race('/api/finished-receipts', [dict(p,request_id=uuid4().hex,quantity='500') for _ in range(2)], h)
    assert [r.status_code for r in results] == [200,200]
    assert quantity() == 2000
    results = race('/api/finished-outbounds', [dict(p,request_id=uuid4().hex,quantity='1500') for _ in range(2)], h)
    assert sorted(r.status_code for r in results) == [200,409]
    assert quantity() == 500
    out = dict(p,request_id=uuid4().hex,quantity='500')
    results = race('/api/finished-outbounds', [out,out], h)
    assert [r.status_code for r in results] == [200,200]
    assert results[0].json()['data'] == results[1].json()['data']
    assert quantity() == 0
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(StockDocument).where(StockDocument.operator_id==ids['user'])) == 5
        assert session.scalar(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.operator_id==ids['user'])) == 5
