from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, func, event, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from test_phase2 import context  # noqa: F401 - 共用真实 MySQL 回滚 fixture
from app import stock_service
from app.models import MaterialBatch, StockDocument, MaterialMovement, InventoryTransaction, OperationLog


@pytest.fixture
def inventory(context):
    client, headers, _, connection, prefix = context
    def create(resource, **extra):
        payload = {'name': prefix, **extra}
        if resource != 'locations': payload['code'] = prefix
        response = client.post('/api/' + resource, headers=headers['admin'],
                               json=payload)
        assert response.status_code == 200, response.text
        return response.json()['data']['id']
    today = stock_service.business_today()
    payload = dict(request_id=uuid4().hex, material_id=create('materials', unit='kg'), batch_no=prefix,
                   quantity='10000', unit='g', supplier_id=create('suppliers'), location_id=create('locations'),
                   production_date=str(today - timedelta(days=10)), expiry_date=str(today + timedelta(days=10)))
    return client, headers, connection, payload


def receive(inv, **changes):
    client, headers, _, payload = inv
    body = {**payload, **changes}
    body.pop('location_id', None)
    return client.post('/api/material-receipts', headers=headers['warehouse'], json=body)


def shelf(inv, response):
    client, headers, _, payload = inv
    detail = client.get(f"/api/material-receipts/{response.json()['data']['id']}", headers=headers['warehouse']).json()['data']
    result = client.post('/api/material-putaways', headers=headers['warehouse'], json={
        'request_id': uuid4().hex, 'receipt_movement_id': detail['receipt_movement_id'],
        'location_id': payload['location_id'], 'quantity': detail['pending_quantity'],
    })
    return result


def out(inv, batch_id, **changes):
    client, headers, _, payload = inv
    data = dict(request_id=uuid4().hex, material_id=payload['material_id'], batch_id=batch_id, quantity='2', unit='kg')
    return client.post('/api/material-outbounds', headers=headers['warehouse'], json={**data, **changes})


def test_receipt_outbound_queries_idempotency(inventory):
    client, h, _, p = inventory
    response = receive(inventory)
    assert response.status_code == 200, response.text
    data = response.json()['data']; batch_id = shelf(inventory, response).json()['data']['target_batch_id']
    assert Decimal(data['after_quantity']) == 10
    assert receive(inventory).json()['data'] == data
    assert receive(inventory, quantity='11000').status_code == 409
    second = receive(inventory, request_id=uuid4().hex, quantity='1000')
    assert second.status_code == 200
    assert Decimal(second.json()['data']['after_quantity']) == 1
    assert shelf(inventory, second).status_code == 200
    mismatch = receive(inventory, request_id=uuid4().hex, expiry_date=str(stock_service.business_today()+timedelta(days=11)))
    assert mismatch.status_code == 200 and shelf(inventory, mismatch).status_code == 409
    request_id = uuid4().hex
    result = out(inventory, batch_id, request_id=request_id)
    assert result.status_code == 200, result.text
    assert Decimal(result.json()['data']['after_quantity']) == 9
    assert out(inventory, batch_id, request_id=request_id).json()['data'] == result.json()['data']
    assert out(inventory, batch_id, quantity='9.000001').status_code == 409
    assert out(inventory, batch_id, quantity='9').status_code == 200
    assert out(inventory, batch_id, quantity='0.000001').status_code == 409
    batches = client.get('/api/material-batches', headers=h['workshop'], params={'material_id':p['material_id']}).json()['data']
    assert batches['total'] == 1 and Decimal(batches['items'][0]['remaining_quantity']) == 0
    summary = client.get('/api/material-stock', headers=h['warehouse'], params={'q':p['batch_no']}).json()['data']
    assert Decimal(summary['items'][0]['remaining_quantity']) == 0
    tx = client.get('/api/material-transactions', headers=h['warehouse'], params={'batch_id':batch_id}).json()['data']
    assert tx['total'] == 4
    for row in tx['items']:
        assert Decimal(row['before_quantity']) + Decimal(row['change_quantity']) == Decimal(row['after_quantity'])
    assert client.get('/api/material-transactions', headers=h['admin'], params={'batch_id':batch_id,'page_size':1,'page':2}).json()['data']['total'] == 4
    assert client.get('/api/material-fefo', headers=h['admin'], params={'material_id':p['material_id']}).json()['data']['total'] == 0
    for method in ('put','delete'):
        assert getattr(client,method)(f'/api/material-batches/{batch_id}',headers=h['admin']).status_code in (404,405)
        assert getattr(client,method)(f'/api/material-transactions/{tx["items"][0]["id"]}',headers=h['admin']).status_code in (404,405)


@pytest.mark.parametrize('change', [dict(quantity='0'),dict(quantity='-1'),dict(quantity=1.2),dict(quantity=True),
    dict(quantity='NaN'),dict(quantity='1.0000001'),dict(quantity='100000000000000'),dict(unit='L'),
    dict(quantity='0.000001',unit='g'),dict(unit='盒'),dict(remaining_quantity='10'),dict(batch_no=' ')])
def test_invalid_receipt(inventory, change):
    assert receive(inventory, **change).status_code == 422


def test_fefo_expiry_manual_and_permissions(inventory, monkeypatch):
    client, h, connection, p = inventory
    today = stock_service.business_today()
    first = shelf(inventory, receive(inventory, expiry_date=str(today))).json()['data']['target_batch_id']
    later = shelf(inventory, receive(inventory, request_id=uuid4().hex, batch_no=p['batch_no']+'later')).json()['data']['target_batch_id']
    recommended = client.get('/api/material-fefo',headers=h['warehouse'],params={'material_id':p['material_id']})
    assert recommended.status_code == 200, recommended.text
    assert [r['id'] for r in recommended.json()['data']['items']] == [first,later]
    assert out(inventory,later).status_code == 200  # 合法人工选批
    assert out(inventory,first,quantity='1').status_code == 200  # 当天有效
    monkeypatch.setattr(stock_service,'business_today',lambda: today+timedelta(days=1))
    # 查询模块也用同一业务日期规则，模拟跨日。
    from app import stock
    monkeypatch.setattr(stock,'business_today',lambda: today+timedelta(days=1))
    assert out(inventory,first).status_code == 422
    assert [r['id'] for r in client.get('/api/material-fefo',headers=h['warehouse'],params={'material_id':p['material_id']}).json()['data']['items']] == [later]
    assert receive(inventory,request_id=uuid4().hex,batch_no='expired',expiry_date=str(today)).status_code == 422
    assert out(inventory,2147483647).status_code == 422
    receipt_body = {key: value for key, value in p.items() if key != 'location_id'}
    assert client.post('/api/material-receipts',headers=h['workshop'],json=receipt_body).status_code == 403
    assert client.post('/api/material-receipts',json=receipt_body).status_code == 401
    assert client.get('/api/material-stock').status_code == 401
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        from app.models import RawMaterial
        session.get(RawMaterial,p['material_id']).is_active=False
        session.commit()
    assert out(inventory,later).status_code == 422


@pytest.mark.parametrize('failure', ['movement','transaction','audit','commit'])
@pytest.mark.parametrize('direction', ['receipt','new_receipt','outbound'])
def test_complete_rollback(inventory, monkeypatch, failure, direction):
    client,h,connection,p=inventory
    batch_id=shelf(inventory, receive(inventory)).json()['data']['target_batch_id']
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        before={model:session.scalar(select(func.count()).select_from(model)) for model in
                (StockDocument,MaterialMovement,InventoryTransaction,OperationLog)}
    if failure=='audit':
        original=stock_service.audit
        def fail(*args,**kwargs):
            original(*args,**kwargs); args[0].flush(); raise RuntimeError('audit failure')
        monkeypatch.setattr(stock_service,'audit',fail)
    else:
        def fail(session,*args):
            if failure=='commit' and session.in_nested_transaction():
                return
            if failure=='commit' or any(isinstance(row,MaterialMovement if failure=='movement' else InventoryTransaction) for row in session.new):
                raise RuntimeError('injected failure')
        event.listen(Session,'before_commit' if failure=='commit' else 'before_flush',fail)
    try:
        if direction=='new_receipt':
            result=receive(inventory,request_id=uuid4().hex,batch_no=p['batch_no']+'rollback')
        else:
            result=receive(inventory,request_id=uuid4().hex) if direction=='receipt' else out(inventory,batch_id)
        assert result.status_code==500,result.text
    finally:
        if failure!='audit':
            event.remove(Session,'before_commit' if failure=='commit' else 'before_flush',fail)
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        assert session.scalar(select(MaterialBatch).where(MaterialBatch.batch_no==p['batch_no']+'rollback')) is None
        assert session.get(MaterialBatch,batch_id).remaining_quantity==Decimal('10')
        assert session.get(MaterialBatch,batch_id).received_quantity==Decimal('10')
        for model,count in before.items():
            assert session.scalar(select(func.count()).select_from(model))==count


def test_ledger_database_protection(inventory):
    _,_,connection,_=inventory
    data=receive(inventory).json()['data']
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        for sql in ('UPDATE inventory_transactions SET notes = NULL WHERE document_id = :id',
                    'DELETE FROM inventory_transactions WHERE document_id = :id',
                    'UPDATE material_batches SET remaining_quantity = -1 WHERE id = :id'):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(text(sql), {'id':data['batch_id'] if 'material_batches' in sql else data['id']})


def test_conversion_dates_and_fefo_ties(inventory):
    client,h,connection,p=inventory
    today=stock_service.business_today()
    for change in ({'production_date':str(today+timedelta(days=1))},
                   {'expiry_date':str(today-timedelta(days=11))}):
        assert receive(inventory,**change).status_code==422
    first=shelf(inventory, receive(inventory)).json()['data']['target_batch_id']
    second=shelf(inventory, receive(inventory,request_id=uuid4().hex,batch_no=p['batch_no']+'2')).json()['data']['target_batch_id']
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        session.get(MaterialBatch,second).received_at=session.get(MaterialBatch,first).received_at
        session.commit()
    assert [r['id'] for r in client.get('/api/material-fefo',headers=h['warehouse'],params={'material_id':p['material_id']}).json()['data']['items']]==[first,second]
    for change in ({'quantity':'-1'},{'quantity':0.5},{'quantity':'NaN'},{'quantity':'0'},{'unit':'L'}):
        assert out(inventory,first,**change).status_code==422
    other=client.post('/api/materials',headers=h['admin'],json={'code':p['batch_no']+'v','name':'volume-test','unit':'L'}).json()['data']['id']
    assert out(inventory,first,material_id=other).status_code==422
    result=receive(inventory,request_id=uuid4().hex,material_id=other,quantity='1000',unit='mL')
    assert result.status_code==200,result.text
    assert Decimal(result.json()['data']['after_quantity'])==1
    # 反向 kg -> g 不丢失精度；整数上限之外的换算拒绝。
    from app.stock_service import convert_quantity
    with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
        assert convert_quantity(session,Decimal('1.123456'),'kg','g')==Decimal('1123.456')
        assert convert_quantity(session,Decimal('1'),'L','mL')==Decimal('1000')
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            convert_quantity(session,Decimal('99999999999999'),'kg','g')
