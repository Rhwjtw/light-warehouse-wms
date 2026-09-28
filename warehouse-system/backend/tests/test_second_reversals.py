"""Second-batch reversal contract, using guarded real least-privilege MySQL."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, func
from sqlalchemy.orm import Session

from app import reversal_service, material_location_service
from app.database import SessionLocal
from app.models import (MaterialBatch, FinishedBatch, MaterialMovement, StockDocument,
                        InventoryTransaction, OperationLog, User)
from app.reconciliation import reconcile
from app.schemas import StockReversalInput, MaterialPutawayInput, MaterialTransferInput
from test_phase2 import context  # noqa: F401
from test_phase3 import inventory, receive, shelf, out
from test_reversals import reverse, assert_production_fact_grants
from test_phase7_concurrency import committed_production  # noqa: F401

pytestmark = pytest.mark.mysql
KINDS = ['material_receipt', 'putaway', 'transfer', 'finished_receipt', 'adjustment']


def data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def post(inv, path, **body):
    if path.startswith('finished-'):
        from app.stock_service import business_today
        body.setdefault('business_date', str(business_today()))
    return inv[0].post('/api/' + path, headers=inv[1]['warehouse'],
                       json={'request_id': uuid4().hex, **body})


def location(inv):
    return data(inv[0].post('/api/locations', headers=inv[1]['warehouse'],
                           json={'name': 'isolated-' + uuid4().hex}))['id']


def setup(inv, kind):
    if kind == 'finished_receipt':
        p = data(inv[0].post('/api/products', headers=inv[1]['admin'],
                            json={'code': inv[3]['batch_no'] + '_fg', 'name': '隔离成品', 'unit': '盒'}))
        return data(post(inv, 'finished-receipts', product_id=p['id'], quantity='10', unit='盒', business_type='normal'))
    receipt = receive(inv)
    if kind == 'material_receipt': return data(receipt)
    put = data(shelf(inv, receipt))
    if kind == 'putaway': return put
    batch = put['target_batch_id']
    if kind == 'transfer':
        return data(post(inv, 'material-transfers', source_batch_id=batch, target_location_id=location(inv), quantity='6'))
    return data(post(inv, 'material-adjustments', batch_id=batch, book_quantity='10', change_quantity='2', reason='隔离调增'))


def checked_session(inv):
    return Session(bind=inv[2], join_transaction_mode='create_savepoint')


def snapshot(session):
    return ([(b.id, b.remaining_quantity, b.received_quantity) for b in session.scalars(select(MaterialBatch).order_by(MaterialBatch.id))],
            [(b.id, b.remaining_quantity) for b in session.scalars(select(FinishedBatch).order_by(FinishedBatch.id))],
            [(m.id, m.shelved_quantity) for m in session.scalars(select(MaterialMovement).order_by(MaterialMovement.id))],
            tuple(session.scalar(select(func.count()).select_from(m)) for m in (StockDocument, InventoryTransaction, OperationLog)))


@pytest.mark.parametrize('kind', KINDS)
def test_normal_whole_reversal_minimal_grants_and_roles(inventory, kind):
    inv = inventory; client, headers, connection, _ = inv
    original = setup(inv, kind); identity = original['id']
    assert_production_fact_grants(connection)
    for role in ('admin', 'warehouse', 'workshop_director', 'workshop', 'accountant'):
        assert client.get(f'/api/stock-documents/{identity}', headers=headers[role]).status_code == 200
        assert data(client.get(f'/api/stock-documents/{identity}/reversal-preview', headers=headers[role]))['eligible']
        if role not in ('admin', 'warehouse'):
            assert reverse(client, headers, identity, role=role).status_code == 403
    assert reverse(client, headers, identity, reason=' ').status_code == 422
    with checked_session(inv) as session:
        gross = {b.id: b.received_quantity for b in session.scalars(select(MaterialBatch))}
        original_rows = list(session.scalars(select(InventoryTransaction).where(InventoryTransaction.document_id == identity)))
        amounts = {r.id: -r.change_quantity for r in original_rows}
    key = uuid4().hex
    result = data(reverse(client, headers, identity, request_id=key))
    assert len(result['transactions']) == (2 if kind in ('putaway', 'transfer') else 1)
    for r in result['transactions']:
        assert Decimal(r['change_quantity']) == amounts[r['reverses_transaction_id']]
        assert Decimal(r['after_quantity']) == Decimal(r['before_quantity']) + Decimal(r['change_quantity'])
    assert data(reverse(client, headers, identity, request_id=key))['id'] == result['id']
    duplicate = reverse(client, headers, identity)
    assert duplicate.status_code == 409 and duplicate.json()['code'] == 'ALREADY_REVERSED'
    assert reverse(client, headers, result['id']).status_code == 409
    assert data(client.get(f'/api/stock-documents/{identity}', headers=headers['admin']))['status'] == 'reversed'
    with checked_session(inv) as session:
        assert {b.id: b.received_quantity for b in session.scalars(select(MaterialBatch))} == gross
        assert reconcile(session)['problems'] == []


def test_partial_putaway_undo_then_receipt_and_server_guard(inventory):
    inv = inventory; client, headers, _, payload = inv
    receipt = data(receive(inv)); identity = receipt['id']
    detail = data(client.get(f'/api/material-receipts/{identity}', headers=headers['warehouse']))
    body = {'receipt_movement_id': detail['receipt_movement_id'], 'location_id': payload['location_id'], 'quantity': '6'}
    put = data(post(inv, 'material-putaways', **body))
    assert reverse(client, headers, identity).json()['code'] == 'DOWNSTREAM_DEPENDENCY'
    data(reverse(client, headers, put['id']))
    d = data(client.get(f'/api/material-receipts/{identity}', headers=headers['warehouse']))
    assert Decimal(d['shelved_quantity']) == 0 and Decimal(d['pending_quantity']) == 10
    data(reverse(client, headers, identity))
    d = data(client.get(f'/api/material-receipts/{identity}', headers=headers['warehouse']))
    assert d['status'] == 'reversed' and Decimal(d['pending_quantity']) == 0
    assert data(client.get('/api/material-receipts', headers=headers['warehouse'], params={'status':'open'}))['total'] == 0
    assert post(inv, 'material-putaways', **body).status_code == 409
    with checked_session(inv) as session: assert reconcile(session)['problems'] == []


@pytest.mark.parametrize('downstream', ['outbound', 'transfer', 'adjustment'])
def test_effective_downstream_then_formal_reversal_unblocks(inventory, downstream):
    inv = inventory; original = setup(inv, 'putaway'); batch = original['target_batch_id']
    if downstream == 'outbound': later = data(out(inv, batch))
    elif downstream == 'transfer': later = data(post(inv,'material-transfers',source_batch_id=batch,target_location_id=location(inv),quantity='2'))
    else: later = data(post(inv,'material-adjustments',batch_id=batch,book_quantity='10',change_quantity='-2',reason='隔离调整'))
    # Refill the same merged batch; sufficient quantity does NOT erase dependency.
    extra = receive(inv, request_id=uuid4().hex, quantity='2000')
    data(shelf(inv, extra))
    response = reverse(inv[0], inv[1], original['id'])
    assert response.status_code == 409 and response.json()['code'] == 'DOWNSTREAM_DEPENDENCY'
    data(reverse(inv[0], inv[1], later['id']))
    result = data(reverse(inv[0], inv[1], original['id']))
    target = next(r for r in result['transactions'] if r['material_batch_id'] == batch)
    assert (Decimal(target['before_quantity']), Decimal(target['after_quantity'])) == (12, 2)
    with checked_session(inv) as session: assert reconcile(session)['problems'] == []


def test_transfer_chain_reverse_in_dependency_order(inventory):
    inv=inventory; first=setup(inv,'transfer')
    second=data(post(inv,'material-transfers',source_batch_id=first['target_batch_id'],target_location_id=location(inv),quantity='2'))
    assert reverse(inv[0],inv[1],first['id']).json()['code']=='DOWNSTREAM_DEPENDENCY'
    data(reverse(inv[0],inv[1],second['id']))
    data(reverse(inv[0],inv[1],first['id']))
    with checked_session(inv) as session: assert reconcile(session)['problems']==[]


@pytest.mark.parametrize('kind', ['putaway','transfer','adjustment'])
def test_zero_count_is_permanent_boundary(inventory, kind):
    inv=inventory; original=setup(inv,kind)
    batch=original.get('target_batch_id',original.get('batch_id'))
    with checked_session(inv) as session: balance=str(session.get(MaterialBatch,batch).remaining_quantity)
    data(post(inv,'material-counts',batch_id=batch,book_quantity=balance,actual_quantity=balance,reason='隔离零差盘点'))
    assert reverse(inv[0],inv[1],original['id']).json()['code']=='DOWNSTREAM_DEPENDENCY'
    with checked_session(inv) as session: assert reconcile(session)['problems']==[]


def test_adjustment_live_balance_both_signs_and_insufficient(inventory):
    inv=inventory; plus=setup(inv,'adjustment'); batch=plus['batch_id']
    data(out(inv,batch,quantity='11'))
    response=reverse(inv[0],inv[1],plus['id'])
    assert response.status_code==409 and response.json()['code']=='INSUFFICIENT_STOCK'
    # Aggregate adjustment contract permits quantity reversal once current stock allows it.
    data(shelf(inv,receive(inv,request_id=uuid4().hex,quantity='5000')))
    reversed_plus=data(reverse(inv[0],inv[1],plus['id']))
    assert Decimal(reversed_plus['transactions'][0]['before_quantity'])==6
    minus=data(post(inv,'material-adjustments',batch_id=batch,book_quantity='4',change_quantity='-3',reason='隔离调减'))
    result=data(reverse(inv[0],inv[1],minus['id']))
    assert Decimal(result['transactions'][0]['after_quantity'])==4
    with checked_session(inv) as session: assert reconcile(session)['problems']==[]


def test_finished_aggregate_insufficient_then_current_balance(inventory):
    inv=inventory; receipt=setup(inv,'finished_receipt')
    with checked_session(inv) as session:
        product=receipt['product_id']
    outgoing=data(post(inv,'finished-outbounds',product_id=product,quantity='9',unit='盒',business_type='normal'))
    assert reverse(inv[0],inv[1],receipt['id']).json()['code']=='INSUFFICIENT_STOCK'
    data(post(inv,'finished-receipts',product_id=product,quantity='10',unit='盒',business_type='normal'))
    result=data(reverse(inv[0],inv[1],receipt['id']))
    assert result['inventory_scope']=='aggregate'
    assert (Decimal(result['transactions'][0]['before_quantity']),Decimal(result['transactions'][0]['after_quantity']))==(11,1)
    with checked_session(inv) as session:
        assert session.scalar(select(func.count()).select_from(FinishedBatch))==1
        assert reconcile(session)['problems']==[]


@pytest.mark.parametrize('kind,failure', [(kind, stage) for kind in KINDS for stage in ('document','stock','audit','ledger')] + [('putaway','progress'),('putaway','second_ledger'),('transfer','second_ledger')])
def test_multirow_reversal_atomic_rollback(inventory, kind, failure):
    inv=inventory; original=setup(inv,kind)
    with checked_session(inv) as session: before=snapshot(session)
    model, hook = {'document':(StockDocument,'after_insert'), 'stock':(FinishedBatch if kind=='finished_receipt' else MaterialBatch,'after_update'),
                   'progress':(MaterialMovement,'after_update'), 'ledger':(InventoryTransaction,'before_insert'), 'second_ledger':(InventoryTransaction,'before_insert'),
                   'audit':(OperationLog,'after_insert')}[failure]
    calls=0
    def fail(mapper, connection, row):
        nonlocal calls
        if failure=='document' and row.kind!='reversal': return
        if failure=='second_ledger':
            if row.kind!='reversal': return
            calls+=1
            if calls!=2: return
        if failure=='audit' and row.action!='reverse_stock_document': return
        raise RuntimeError('isolated injected failure')
    event.listen(model,hook,fail)
    try:
        response=reverse(inv[0],inv[1],original['id'])
        assert response.status_code==500 and response.json()['code']=='INTERNAL_ERROR'
        assert 'injected' not in response.text
    finally: event.remove(model,hook,fail)
    with checked_session(inv) as session:
        assert snapshot(session)==before
        assert reconcile(session)['problems']==[]


def committed_inventory(ctx):
    client, headers, _, prefix=ctx
    from app.stock_service import business_today
    from datetime import timedelta
    def create(resource, **fields):
        body={'name':prefix, **fields}
        if resource!='locations': body['code']=prefix
        return data(client.post('/api/'+resource,headers=headers['admin'],json=body))['id']
    return client,headers,None,dict(request_id=uuid4().hex,material_id=create('materials',unit='kg'),
        supplier_id=create('suppliers'),location_id=create('locations'),batch_no=prefix,quantity='10',unit='kg',
        production_date=str(business_today()),expiry_date=str(business_today()+timedelta(days=10)))


def run_race(ctx, functions):
    gate=Barrier(2)
    def task(fn):
        cid=None
        try:
            with SessionLocal.begin() as session:
                cid=session.connection().exec_driver_sql('SELECT CONNECTION_ID()').scalar_one()
                assert_production_fact_grants(session.connection())
                user=session.scalar(select(User).where(User.username==ctx[3]))
                gate.wait(timeout=15)
                fn(session,user)
            return cid,200,None
        except HTTPException as exc: return cid,exc.status_code,getattr(exc,'code',None)
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(task,functions))
    assert len({r[0] for r in results})==2
    with SessionLocal() as session: assert reconcile(session)['problems']==[]
    return results


def reversal(identity, key=None):
    return lambda session,user: reversal_service.reverse_document(session,user,identity,
        StockReversalInput(request_id=key or uuid4().hex,reason='隔离真实双连接竞争'))


@pytest.mark.parametrize('kind', KINDS)
def test_two_connection_same_original(committed_production,kind):
    ctx=committed_production; inv=committed_inventory(ctx); original=setup(inv,kind)
    results=run_race(ctx,[reversal(original['id']),reversal(original['id'])])
    assert sorted(r[1] for r in results)==[200,409]
    assert [r[2] for r in results if r[1]==409]==['ALREADY_REVERSED']
    with SessionLocal() as session:
        inverse=session.scalars(select(StockDocument).where(StockDocument.reverses_document_id==original['id'])).all()
        assert len(inverse)==1
        assert session.scalar(select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.document_id==inverse[0].id))==(2 if kind in ('putaway','transfer') else 1)


def test_same_request_two_connections_returns_same_result(committed_production):
    ctx=committed_production; inv=committed_inventory(ctx); original=setup(inv,'putaway'); key=uuid4().hex
    results=run_race(ctx,[reversal(original['id'],key),reversal(original['id'],key)])
    assert [r[1] for r in results]==[200,200]


def test_receipt_reversal_vs_putaway(committed_production):
    ctx=committed_production; inv=committed_inventory(ctx); receipt=setup(inv,'material_receipt')
    movement=data(inv[0].get(f"/api/material-receipts/{receipt['id']}",headers=inv[1]['warehouse']))['receipt_movement_id']
    put=lambda s,u: material_location_service.putaway(s,u,MaterialPutawayInput(request_id=uuid4().hex,
        receipt_movement_id=movement,location_id=inv[3]['location_id'],quantity='10'))
    results=run_race(ctx,[reversal(receipt['id']),put])
    assert sorted(r[1] for r in results)==[200,409]


def test_transfer_reversal_vs_normal_transfer(committed_production):
    ctx=committed_production; inv=committed_inventory(ctx); original=setup(inv,'transfer'); target=location(inv)
    move=lambda s,u: material_location_service.transfer(s,u,MaterialTransferInput(request_id=uuid4().hex,
        source_batch_id=original['target_batch_id'],target_location_id=target,quantity='1'))
    results=run_race(ctx,[reversal(original['id']),move])
    assert sorted(r[1] for r in results)==[200,409]


def test_upstream_vs_downstream_reversal(committed_production):
    ctx=committed_production; inv=committed_inventory(ctx); original=setup(inv,'transfer')
    later=data(post(inv,'material-transfers',source_batch_id=original['target_batch_id'],target_location_id=location(inv),quantity='2'))
    results=run_race(ctx,[reversal(original['id']),reversal(later['id'])])
    assert results[1][1]==200 and results[0][1] in (200,409)
    if results[0][1]==409: data(reverse(inv[0],inv[1],original['id']))
    with SessionLocal() as session: assert reconcile(session)['problems']==[]


def test_production_dependencies_and_completed_receipt_remain_excluded(committed_production):
    from test_phase7 import setup_order, settle, post_material
    from app.models import MaterialTransfer, FinishedReceipt
    ctx=committed_production; scenario=setup_order(ctx,'secondblock')
    batch=scenario['batches'][0]
    with SessionLocal() as session:
        put_id=session.scalar(select(MaterialTransfer.document_id).where(MaterialTransfer.target_batch_id==batch))
    client,headers=ctx[:2]
    assert reverse(client,headers,put_id).json()['code']=='DOWNSTREAM_DEPENDENCY'
    data(post_material(scenario,'supplement',scenario['order']['materials'][0]['id'],[{'batch_id':batch,'quantity':'1'}]))
    data(post_material(scenario,'return',scenario['order']['materials'][0]['id'],[{'batch_id':batch,'quantity':'21'}]))
    # Returning everything is not an authorized reversal of production facts.
    assert reverse(client,headers,put_id).json()['code']=='DOWNSTREAM_DEPENDENCY'
    data(settle(scenario))
    with SessionLocal() as session:
        receipt=session.scalar(select(FinishedReceipt.document_id).where(FinishedReceipt.production_order_id==scenario['order']['id']))
    assert reverse(client,headers,receipt).json()['code']=='UNSUPPORTED_REVERSAL'
    with SessionLocal() as session: assert reconcile(session)['problems']==[]


@pytest.mark.parametrize('corruption', ['location','audit','cross_count'])
def test_reconciliation_detects_evidenced_corruption(inventory, monkeypatch, corruption):
    inv=inventory; original=setup(inv,'putaway')
    if corruption=='cross_count':
        data(post(inv,'material-counts',batch_id=original['target_batch_id'],book_quantity='10',actual_quantity='10',reason='隔离边界'))
        # Simulate a faulty executor bypass. Reconciliation must independently detect it.
        monkeypatch.setattr(reversal_service,'downstream_blockers',lambda *args,**kwargs: [])
        data(reverse(inv[0],inv[1],original['id']))
    else:
        model=InventoryTransaction if corruption=='location' else OperationLog
        def corrupt(mapper,conn,row):
            if corruption=='location' and row.kind=='reversal': row.source_location_id=None
            if corruption=='audit' and row.action=='reverse_stock_document': row.details={**row.details,'original_document_id':-1}
        event.listen(model,'before_insert',corrupt)
        try: data(reverse(inv[0],inv[1],original['id']))
        finally: event.remove(model,'before_insert',corrupt)
    with checked_session(inv) as session:
        result=reconcile(session)
        assert not result['ok'] and any(p['type']=='reversal' for p in result['problems'])


def test_locked_putaway_refreshes_preloaded_progress(inventory):
    inv=inventory; client,headers,_,_=inv
    receipt=data(receive(inv))
    detail=data(client.get(f"/api/material-receipts/{receipt['id']}",headers=headers['warehouse']))
    movement=detail['receipt_movement_id']
    with checked_session(inv) as session:
        stale=session.get(MaterialMovement,movement)
        assert stale.shelved_quantity==0
        first=data(post(inv,'material-putaways',receipt_movement_id=movement,location_id=inv[3]['location_id'],quantity='4'))
        assert stale.shelved_quantity==0
        user=session.scalar(select(User).where(User.username.like(inv[3]['batch_no']+'%'),User.display_name=='warehouse'))
        # Cached before acquiring the mutex: refresh must see the 4 already posted.
        material_location_service.putaway(session,user,MaterialPutawayInput(request_id=uuid4().hex,
            receipt_movement_id=movement,location_id=inv[3]['location_id'],quantity='6'))
        assert stale.shelved_quantity==10
        reversal_service.reverse_document(session,user,first['id'],StockReversalInput(request_id=uuid4().hex,reason='锁后刷新验证'))
        assert stale.shelved_quantity==6
        assert reconcile(session)['problems']==[]


@pytest.mark.parametrize('kind', KINDS)
def test_unique_constraint_race_is_409_and_rolls_back(inventory, kind):
    inv=inventory; original=setup(inv,kind)
    with checked_session(inv) as session: before=snapshot(session)
    def collision(mapper,connection,row):
        if row.kind!='reversal': return
        connection.exec_driver_sql('INSERT INTO stock_documents '
            '(document_no,request_id,kind,reverses_document_id,reverses_kind,original_document_no_snapshot,reversal_reason,operator_id,created_at) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())',
            ('RV-isolated-'+uuid4().hex,uuid4().hex,'reversal',row.reverses_document_id,
             row.reverses_kind,row.original_document_no_snapshot,'isolated collision',row.operator_id))
    event.listen(StockDocument,'before_insert',collision)
    try:
        response=reverse(inv[0],inv[1],original['id'])
        assert response.status_code==409 and response.json()['code']=='ALREADY_REVERSED'
    finally: event.remove(StockDocument,'before_insert',collision)
    with checked_session(inv) as session:
        assert snapshot(session)==before and reconcile(session)['problems']==[]


def test_fact_sql_mutations_are_denied_by_mysql(inventory):
    from sqlalchemy.exc import DBAPIError
    inv=inventory; setup(inv,'putaway')
    connection=inv[2]
    for table in ('stock_documents','inventory_transactions','operation_logs','material_transfers',
                  'material_adjustments','material_counts','finished_receipts','finished_outbounds','material_issue_records'):
        for sql in (f'UPDATE {table} SET id=id WHERE 1=0',f'DELETE FROM {table} WHERE 1=0',f'SELECT * FROM {table} FOR UPDATE'):
            with pytest.raises(DBAPIError) as caught: connection.exec_driver_sql(sql)
            assert caught.value.orig.args[0] in (1142,1143)


def test_commit_failure_rolls_back_finished_reversal(inventory):
    from sqlalchemy.orm import object_session
    inv=inventory; original=setup(inv,'finished_receipt')
    with checked_session(inv) as session: before=snapshot(session)
    def mark(mapper,connection,row):
        if row.action=='reverse_stock_document': object_session(row).info['fail_final_commit']=True
    def fail(session):
        if session.info.get('fail_final_commit'): raise RuntimeError('isolated final commit failure')
    event.listen(OperationLog,'after_insert',mark); event.listen(Session,'before_commit',fail)
    try: assert reverse(inv[0],inv[1],original['id']).status_code==500
    finally:
        event.remove(OperationLog,'after_insert',mark); event.remove(Session,'before_commit',fail)
    with checked_session(inv) as session:
        assert snapshot(session)==before and reconcile(session)['problems']==[]
