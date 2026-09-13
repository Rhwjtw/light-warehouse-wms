from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, func, event, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError, IntegrityError

from test_phase2 import context  # noqa: F401
from app import finished_service
from app.models import (FinishedBatch, FinishedReceipt, FinishedOutbound, FinishedProduct,
                        StockDocument, InventoryTransaction, OperationLog)


@pytest.fixture
def finished_inventory(context):
    client, headers, _, connection, prefix = context
    response = client.post('/api/products', headers=headers['admin'], json={'code':prefix,'name':prefix,'unit':'盒'})
    assert response.status_code == 200, response.text
    payload = dict(product_id=response.json()['data']['id'], quantity='1000', unit='盒',
                   business_date=str(finished_service.business_today()), business_type='normal',
                   reference_no='REF-'+prefix, notes='成品测试')
    return client, headers, connection, payload


def post(inv, path='finished-receipts', role='warehouse', **changes):
    client, h, _, p = inv
    return client.post('/api/'+path, headers=h[role], json={**p,'request_id':uuid4().hex,**changes})


def balance(inv):
    client,h,_,p=inv
    response=client.get('/api/finished-stock/'+str(p['product_id']),headers=h['warehouse'])
    assert response.status_code==200,response.text
    return response.json()['data']


def test_finished_receipts_outbounds_and_queries(finished_inventory):
    inv=finished_inventory
    client,h,connection,p=inv
    initial=balance(inv)
    assert Decimal(initial['quantity'])==0 and initial['last_receipt_at'] is None and initial['last_outbound_at'] is None
    first=post(inv)
    assert first.status_code==200,first.text
    assert Decimal(first.json()['data']['after_quantity'])==1000
    assert post(inv,quantity='500').status_code==200
    assert Decimal(balance(inv)['quantity'])==1500
    out=post(inv,'finished-outbounds',quantity='200')
    assert out.status_code==200,out.text
    assert Decimal(balance(inv)['quantity'])==1300
    assert post(inv,'finished-outbounds',quantity='1500').status_code==409
    assert Decimal(balance(inv)['quantity'])==1300
    assert balance(inv)['last_receipt_at'] and balance(inv)['last_outbound_at']
    for path,expected in [('finished-receipts',2),('finished-outbounds',1),('finished-transactions',3)]:
        response=client.get('/api/'+path,headers=h['workshop'],params={'product_id':p['product_id'],'page_size':1,'page':2})
        assert response.status_code==200,response.text
        assert response.json()['data']['total']==expected
    detail=client.get('/api/finished-receipts/'+str(first.json()['data']['id']),headers=h['warehouse']).json()['data']
    assert detail['reference_no']==p['reference_no'] and detail['production_order_id'] is None
    assert detail['business_date']==p['business_date'] and detail['business_type']=='normal'
    assert client.get('/api/finished-outbounds/'+str(first.json()['data']['id']),headers=h['warehouse']).status_code==404
    rows=client.get('/api/finished-transactions',headers=h['warehouse'],params={'product_id':p['product_id']}).json()['data']['items']
    assert [Decimal(r['change_quantity']) for r in rows]==[-200,500,1000]
    for r in rows:
        assert Decimal(r['before_quantity'])+Decimal(r['change_quantity'])==Decimal(r['after_quantity'])
    name=initial['name']
    assert client.get('/api/finished-stock',headers=h['warehouse'],params={'q':name}).json()['data']['total']==1
    assert client.get('/api/finished-stock',headers=h['warehouse'],params={'q':'%'}).json()['data']['total']==0
    for path in ('finished-stock','finished-receipts','finished-outbounds','finished-transactions'):
        assert client.get('/api/'+path,headers=h['warehouse'],params={'page_size':101}).status_code==422
        assert client.get('/api/'+path).status_code==401
    assert post(inv,'finished-outbounds',quantity='1300').status_code==200
    assert post(inv,'finished-outbounds',quantity='0.000001').status_code==409
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        assert session.scalar(select(func.count()).select_from(FinishedBatch).where(FinishedBatch.product_id==p['product_id']))==1


@pytest.mark.parametrize('unit',['瓶','支','套','个'])
def test_product_unit_is_not_hardcoded(finished_inventory,unit):
    client,h,_,p=finished_inventory
    product=client.get('/api/products/'+str(p['product_id']),headers=h['admin']).json()['data']
    assert client.put('/api/products/'+str(p['product_id']),headers=h['admin'],json={'code':product['code'],'name':product['name'],'unit':unit}).status_code==200
    assert post(finished_inventory,unit=unit,quantity='1.000001').status_code==200
    assert balance(finished_inventory)['unit']==unit
    assert Decimal(balance(finished_inventory)['quantity'])==Decimal('1.000001')
    assert post(finished_inventory,'finished-outbounds',unit=unit,quantity='0.000001').status_code==200
    assert post(finished_inventory,unit='盒').status_code==422
    assert client.put('/api/products/'+str(p['product_id']),headers=h['admin'],json={'code':product['code'],'name':product['name'],'unit':'盒'}).status_code==409


@pytest.mark.parametrize('quantity',['0','-1',1.2,True,'NaN','Infinity','1.0000001','100000000000000'])
def test_invalid_finished_quantities(finished_inventory,quantity):
    for path in ('finished-receipts','finished-outbounds'):
        assert post(finished_inventory,path,quantity=quantity).status_code==422
    assert Decimal(balance(finished_inventory)['quantity'])==0


def test_finished_permissions_dates_idempotency(finished_inventory):
    inv=finished_inventory
    client,h,connection,p=inv
    assert post(inv,role='workshop').status_code==403
    assert post(inv,'finished-outbounds',role='workshop').status_code==403
    for path in ('finished-receipts','finished-outbounds'):
        for change in ({'unit':'kg'},{'business_type':'production'}, {'production_order_id':1}, {'remaining_quantity':'1000'},
                       {'business_date':'bad'}, {'business_date':'0001-01-01'}, {'business_date':str(finished_service.business_today()+timedelta(days=1))}):
            assert post(inv,path,**change).status_code==422
    key=uuid4().hex
    first=post(inv,request_id=key,role='admin')
    assert first.status_code==200,first.text
    assert post(inv,request_id=key,role='admin',quantity='1000.000000').json()['data']==first.json()['data']
    assert post(inv,request_id=key,role='admin',quantity='1001').status_code==409
    assert post(inv,request_id=key).status_code==409
    assert post(inv,'finished-outbounds',request_id=key,role='admin').status_code==409
    assert Decimal(balance(inv)['quantity'])==1000
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        session.get(FinishedProduct,p['product_id']).is_active=False
        session.commit()
    assert post(inv).status_code==422
    assert Decimal(balance(inv)['quantity'])==1000


@pytest.mark.parametrize('direction',['new_receipt','receipt','outbound'])
@pytest.mark.parametrize('failure',['movement','ledger','audit','commit'])
def test_finished_atomic_rollback(finished_inventory,monkeypatch,direction,failure):
    inv=finished_inventory
    _,_,connection,p=inv
    if direction!='new_receipt':
        assert post(inv).status_code==200
    tracked=(FinishedBatch,FinishedReceipt,FinishedOutbound,StockDocument,InventoryTransaction,OperationLog)
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        counts={m:session.scalar(select(func.count()).select_from(m)) for m in tracked}
    if failure=='audit':
        original=finished_service.audit
        def fail(*args,**kwargs):
            original(*args,**kwargs); args[0].flush(); raise RuntimeError('injected audit failure')
        monkeypatch.setattr(finished_service,'audit',fail)
    else:
        def fail(session,*args):
            if failure=='commit' and session.in_nested_transaction():
                return
            if failure=='commit' or any(isinstance(r, (FinishedReceipt,FinishedOutbound) if failure=='movement' else InventoryTransaction) for r in session.new):
                raise RuntimeError('injected failure')
        event.listen(Session,'before_commit' if failure=='commit' else 'before_flush',fail)
    try:
        result=post(inv,'finished-outbounds' if direction=='outbound' else 'finished-receipts',quantity='10')
        assert result.status_code==500,result.text
    finally:
        if failure!='audit':
            event.remove(Session,'before_commit' if failure=='commit' else 'before_flush',fail)
    assert Decimal(balance(inv)['quantity'])==(0 if direction=='new_receipt' else 1000)
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        for model,count in counts.items():
            assert session.scalar(select(func.count()).select_from(model))==count


def test_finished_database_protection(finished_inventory):
    client,h,connection,p=finished_inventory
    result=post(finished_inventory)
    identity=result.json()['data']['id']
    for method in ('put','delete'):
        for path in ('finished-stock','finished-receipts','finished-outbounds','finished-transactions'):
            assert getattr(client,method)(f'/api/{path}/{identity}',headers=h['admin']).status_code in (404,405)
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        batch=session.scalar(select(FinishedBatch).where(FinishedBatch.product_id==p['product_id']))
        for sql,identity in [('UPDATE finished_batches SET remaining_quantity = -1 WHERE id = :id',batch.id),
                             ('DELETE FROM inventory_transactions WHERE document_id = :id',identity),
                             ('UPDATE inventory_transactions SET notes = NULL WHERE document_id = :id',identity)]:
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(text(sql),{'id':identity})
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(FinishedBatch(product_id=p['product_id'],batch_no='__aggregate__',stock_scope='aggregate',unit=p['unit'],remaining_quantity=Decimal('0')))
                session.flush()


def test_finished_overflow_and_backdated_business_date(finished_inventory):
    inv=finished_inventory
    client,h,_,p=inv
    yesterday=str(finished_service.business_today()-timedelta(days=1))
    result=post(inv,quantity='99999999999999.999999',business_date=yesterday)
    assert result.status_code==200,result.text
    assert result.json()['data']['business_date']==yesterday
    assert post(inv,quantity='0.000001').status_code==422
    assert Decimal(balance(inv)['quantity'])==Decimal('99999999999999.999999')
    name=balance(inv)['name']
    assert client.get('/api/material-transactions',headers=h['warehouse'],params={'q':name}).json()['data']['total']==0
    assert post(inv,'finished-outbounds',quantity='99999999999999.999999').status_code==200
    assert Decimal(balance(inv)['quantity'])==0
