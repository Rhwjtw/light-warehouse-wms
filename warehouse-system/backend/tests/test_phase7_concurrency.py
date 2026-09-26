"""Production completion uses independent connections and real commits; scoped cleanup."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select, func
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import (User, Role, ProductionOrder, ProductionOrderMaterial, MaterialIssueRecord,
                        FinishedReceipt, FinishedBatch, FinishedProduct, BomHeader, BomItem,
                        MaterialBatch, MaterialMovement, MaterialTransfer, RawMaterial, Supplier, Location,
                        InventoryTransaction, StockDocument, OperationLog)
from test_phase6 import PASSWORD
from test_phase7 import setup_order, settle, balance, post_material


@pytest.fixture
def committed_production():
    settings = get_settings()
    assert settings.app_env == 'development' and settings.mysql_database == 'warehouse_system'
    assert settings.mysql_host in ('127.0.0.1', 'localhost')
    prefix = 'p7_' + uuid4().hex[:10]
    with SessionLocal.begin() as session:
        user = User(username=prefix, display_name='第七阶段临时验收', password_hash=hash_password(PASSWORD),
                    role_id=session.scalar(select(Role.id).where(Role.code == 'admin')))
        session.add(user); session.flush(); user_id = user.id
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            token = client.post('/api/auth/login', json={'username': prefix, 'password': PASSWORD}).json()['data']['access_token']
            headers = {role: {'Authorization': 'Bearer ' + token} for role in ('admin','warehouse','workshop_director','workshop')}
            yield client, headers, None, prefix
    finally:
        cleanup = create_engine(settings.database_url.set(username=settings.mysql_migration_user,
                                 password=settings.mysql_migration_password.get_secret_value()))
        with Session(cleanup) as session, session.begin():
            assert session.get(User, user_id).username == prefix
            orders = list(session.scalars(select(ProductionOrder.id).where(ProductionOrder.created_by == user_id)))
            materials = list(session.scalars(select(ProductionOrderMaterial.id).where(ProductionOrderMaterial.production_order_id.in_(orders))))
            docs = list(session.scalars(select(StockDocument.id).where(StockDocument.operator_id == user_id)))
            # 自关联外键要求先清理冲销流水和冲销单，再清理原始记录。
            reversal_docs = list(session.scalars(select(StockDocument.id).where(
                StockDocument.id.in_(docs), StockDocument.kind == 'reversal')))
            if reversal_docs:
                session.execute(delete(InventoryTransaction).where(InventoryTransaction.document_id.in_(reversal_docs)))
                session.execute(delete(StockDocument).where(StockDocument.id.in_(reversal_docs)))
                docs = [identity for identity in docs if identity not in reversal_docs]
            products = list(session.scalars(select(FinishedProduct.id).where(FinishedProduct.code.startswith(prefix))))
            raw = list(session.scalars(select(RawMaterial.id).where(RawMaterial.code.startswith(prefix))))
            boms = list(session.scalars(select(BomHeader.id).where(BomHeader.product_id.in_(products))))
            for model, condition in [
                (InventoryTransaction, InventoryTransaction.document_id.in_(docs)),
                (MaterialIssueRecord, MaterialIssueRecord.order_material_id.in_(materials)),
                (MaterialTransfer, MaterialTransfer.document_id.in_(docs)),
                (MaterialMovement, MaterialMovement.document_id.in_(docs)),
                (FinishedReceipt, FinishedReceipt.document_id.in_(docs)),
                (OperationLog, OperationLog.user_id == user_id),
                (StockDocument, StockDocument.id.in_(docs)),
                (ProductionOrderMaterial, ProductionOrderMaterial.id.in_(materials)),
                (ProductionOrder, ProductionOrder.id.in_(orders)),
                (FinishedBatch, FinishedBatch.product_id.in_(products)),
                (BomItem, BomItem.bom_id.in_(boms)), (BomHeader, BomHeader.id.in_(boms)),
                (MaterialBatch, MaterialBatch.material_id.in_(raw)),
                (RawMaterial, RawMaterial.id.in_(raw)), (FinishedProduct, FinishedProduct.id.in_(products)),
                (Supplier, Supplier.code.startswith(prefix)), (Location, Location.code.startswith(prefix)),
                (User, User.id == user_id),
            ]:
                session.execute(delete(model).where(condition))
        cleanup.dispose()


@pytest.mark.mysql
def test_concurrent_completion_and_material_posting(committed_production):
    data = setup_order(committed_production, 'race')
    barrier = Barrier(2)
    def finish():
        barrier.wait(timeout=10)
        return settle(data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finish(), range(2)))
    assert sorted(result.status_code for result in results) == [200,409]
    assert balance(data) == 980
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(FinishedReceipt).where(
            FinishedReceipt.production_order_id == data['order']['id'])) == 1
    data = setup_order(committed_production, 'materialrace')
    barrier = Barrier(2)
    def operation(kind):
        barrier.wait(timeout=10)
        if kind == 'complete':
            return settle(data)
        return post_material(data, 'supplement', data['order']['materials'][0]['id'],
                             [{'batch_id': data['batches'][0], 'quantity': '1'}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        completed, posted = list(pool.map(operation, ['complete','supplement']))
    assert completed.status_code == 200
    assert posted.status_code in (200,409)
    assert Decimal(completed.json()['data']['materials'][0]['actual_used_quantity']) == (21 if posted.status_code == 200 else 20)
