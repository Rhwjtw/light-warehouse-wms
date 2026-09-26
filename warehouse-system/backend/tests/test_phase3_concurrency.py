"""仅 development 库：独立连接真实提交竞态；按本次随机用户/档案 ID 精确清理。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import (User, Role, RawMaterial, Supplier, Location, MaterialBatch, StockDocument,
                        MaterialMovement, MaterialTransfer, InventoryTransaction, OperationLog, utcnow)
from app.stock_service import business_today


@pytest.fixture
def committed_inventory():
    settings = get_settings()
    if settings.app_env != 'development' or settings.mysql_database != 'warehouse_system':
        pytest.skip('真实提交测试仅允许本地 development warehouse_system')
    prefix='test_concurrent_'+uuid4().hex[:12]
    ids={}
    with SessionLocal.begin() as session:
        user=User(username=prefix,display_name=prefix,role_id=session.scalar(select(Role.id).where(Role.code=='warehouse')),
                  password_hash='!unusable-test-account')
        material=RawMaterial(code=prefix,name=prefix,unit='kg')
        supplier=Supplier(code=prefix,name=prefix)
        location=Location(code=prefix,name=prefix)
        session.add_all([user,material,supplier,location]);session.flush()
        ids=dict(user=user.id,material=material.id,supplier=supplier.id,location=location.id)
    now=utcnow()
    token=jwt.encode({'sub':str(ids['user']),'ver':0,'iat':now,'exp':now+timedelta(minutes=10),
                      'iss':'warehouse-api','aud':'warehouse'},settings.auth_secret.get_secret_value(),algorithm='HS256')
    headers={'Authorization':'Bearer '+token}
    p=dict(request_id=uuid4().hex,material_id=ids['material'],batch_no=prefix,quantity='10',unit='kg',
           supplier_id=ids['supplier'],production_date=str(business_today()),
           expiry_date=str(business_today()+timedelta(days=10)))
    try:
        yield headers,p,ids
    finally:
        # 应用账号不能删流水；仅测试清理使用迁移账号，目标均限定本次随机测试用户/档案。
        cleanup=create_engine(settings.database_url.set(username=settings.mysql_migration_user,
                              password=settings.mysql_migration_password.get_secret_value()))
        with Session(cleanup) as session, session.begin():
            assert session.get(User,ids['user']).username==prefix
            docs=select(StockDocument.id).where(StockDocument.operator_id==ids['user'])
            session.execute(delete(InventoryTransaction).where(InventoryTransaction.operator_id==ids['user']))
            session.execute(delete(MaterialTransfer).where(MaterialTransfer.document_id.in_(docs)))
            session.execute(delete(MaterialMovement).where(MaterialMovement.document_id.in_(docs)))
            session.execute(delete(OperationLog).where(OperationLog.user_id==ids['user']))
            session.execute(delete(StockDocument).where(StockDocument.operator_id==ids['user']))
            session.execute(delete(MaterialBatch).where(MaterialBatch.material_id==ids['material']))
            for model,key in ((RawMaterial,'material'),(Supplier,'supplier'),(Location,'location'),(User,'user')):
                session.execute(delete(model).where(model.id==ids[key]))
        cleanup.dispose()


def race(path, payloads, headers):
    barrier=Barrier(len(payloads))
    def send(payload):
        with TestClient(app,raise_server_exceptions=False) as client:
            barrier.wait(timeout=10)
            return client.post(path,headers=headers,json=payload)
    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(send,payloads))


def test_concurrent_receipt_and_outbound(committed_inventory):
    h,p,ids=committed_inventory
    results=race('/api/material-receipts',[p,p],h)
    assert [r.status_code for r in results]==[200,200]
    assert results[0].json()['data']==results[1].json()['data']
    receipt_id=results[0].json()['data']['id']
    with TestClient(app,raise_server_exceptions=False) as client:
        movement_id=client.get(f'/api/material-receipts/{receipt_id}',headers=h).json()['data']['receipt_movement_id']
    putaway=dict(request_id=uuid4().hex,receipt_movement_id=movement_id,location_id=ids['location'],quantity='10')
    results=race('/api/material-putaways',[putaway,putaway],h)
    assert [r.status_code for r in results]==[200,200]
    batch_id=results[0].json()['data']['target_batch_id']
    out=dict(material_id=ids['material'],batch_id=batch_id,quantity='7',unit='kg')
    results=race('/api/material-outbounds',[dict(out,request_id=uuid4().hex),dict(out,request_id=uuid4().hex)],h)
    assert sorted(r.status_code for r in results)==[200,409]
    with SessionLocal() as session:
        assert session.get(MaterialBatch,batch_id).remaining_quantity==3
        assert session.scalar(select(func.count()).select_from(StockDocument).where(StockDocument.operator_id==ids['user']))==3
        assert session.scalar(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.operator_id==ids['user']))==4
    # 重复并发出库只能扣一次，清零后再次重试仍返回历史成功结果。
    out.update(quantity='3',request_id=uuid4().hex)
    results=race('/api/material-outbounds',[out,out],h)
    assert [r.status_code for r in results]==[200,200]
    assert results[0].json()['data']==results[1].json()['data']
    with SessionLocal() as session:
        assert session.get(MaterialBatch,batch_id).remaining_quantity==0
    results=race('/api/material-receipts',[dict(p,request_id=uuid4().hex),dict(p,request_id=uuid4().hex)],h)
    assert [r.status_code for r in results]==[200,200]
    with TestClient(app,raise_server_exceptions=False) as client:
        for receipt in results:
            receipt_id=receipt.json()['data']['id']
            movement_id=client.get(f'/api/material-receipts/{receipt_id}',headers=h).json()['data']['receipt_movement_id']
            result=client.post('/api/material-putaways',headers=h,json={
                'request_id':uuid4().hex,'receipt_movement_id':movement_id,
                'location_id':ids['location'],'quantity':'10'})
            assert result.status_code==200,result.text
    with SessionLocal() as session:
        assert session.get(MaterialBatch,batch_id).remaining_quantity==20
        assert session.get(MaterialBatch,batch_id).received_quantity==30
