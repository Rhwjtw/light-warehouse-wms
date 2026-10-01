"""Phase 8 uses real MySQL and rolled-back API transactions."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, func, event
from sqlalchemy.orm import Session

from app.models import (MaterialBatch, WarningRule, RawMaterial, OperationLog, InventoryTransaction,
                        ProductionOrder, FinishedProduct, User)
from app.overview import date_bounds
from app.stock_service import business_today
from test_phase6 import context, scenario, post_material, detail
from test_phase7 import settle

pytestmark = pytest.mark.mysql


def get(context, path, **params):
    result = context[0].get('/api/' + path, headers=context[1]['admin'], params=params)
    assert result.status_code == 200, result.text
    return result.json()['data']


@pytest.mark.parametrize('days,state', [(-1,'EXPIRED'), (0,'URGENT'), (30,'URGENT'), (31,'WARNING'), (90,'WARNING'), (91,'NORMAL')])
def test_expiry_boundaries(context, days, state):
    data = scenario(context, 'expiry'+str(days), stock_batches=((0,'2',100),))
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as s:
        for rule in s.scalars(select(WarningRule)):
            rule.days = 30 if rule.code == 'expiry_near' else 90
        s.commit()
    params = {'q': data['materials'][0]['name'], 'status': state}
    from unittest.mock import patch
    test_day = business_today() + timedelta(days=100-days)
    with patch('app.overview.business_today', return_value=test_day):
        result = get(context, 'alerts/expiry', **params)
        assert result['total'] == 1
        row = result['items'][0]
        assert row['status'] == state and row['days_remaining'] == days
        assert row['supplier_name'] and row['location_name'] and row['unit'] == 'kg'
        assert get(context, 'alerts/expiry', **params, page=2)['items'] == []


def test_zero_stock_filters_and_live_settings(context):
    data = scenario(context, 'settings', stock_batches=((0,'1',45), (0,'1',20)))
    client, headers, connection, _ = context
    q = data['materials'][0]['name']
    with Session(bind=connection, join_transaction_mode='create_savepoint') as s:
        batch = s.get(MaterialBatch, data['batches'][1]); batch.remaining_quantity = 0
        s.commit()
    from unittest.mock import patch
    with patch('app.overview.business_today', return_value=business_today()+timedelta(days=21)):
        assert get(context, 'alerts/expiry', q=q)['total'] == 1
        assert get(context, 'alerts/expiry', q=q, status='EXPIRED')['total'] == 0
        result = client.put('/api/settings/expiry', headers=headers['admin'], json={'urgent_expiry_days':50,'warning_expiry_days':100})
        assert result.status_code == 200
        assert get(context, 'alerts/expiry', q=q)['items'][0]['status'] == 'URGENT'
        assert get(context, 'dashboard/summary')['urgent_expiry_days'] == 50
        assert get(context, 'alerts/expiry', q=q, date_from=str(business_today()+timedelta(days=46)))['total'] == 0
        assert get(context, 'alerts/expiry', q='%')['total'] == 0
        assert client.get('/api/alerts/expiry', headers=headers['admin'], params={'date_from':'2026-02-02','date_to':'2026-01-01'}).status_code == 422
        client.put('/api/settings/expiry', headers=headers['admin'], json={'urgent_expiry_days':30,'warning_expiry_days':90})


@pytest.mark.parametrize('urgent,warning', [(0,90),(-1,90),(90,30),(30,30),(True,90),(1.5,90),(30,36501)])
def test_invalid_settings(context, urgent, warning):
    before = get(context, 'settings/expiry')
    r = context[0].put('/api/settings/expiry', headers=context[1]['admin'], json={'urgent_expiry_days':urgent,'warning_expiry_days':warning})
    assert r.status_code == 422 and get(context, 'settings/expiry') == before


@pytest.mark.parametrize('role', ['warehouse','workshop_director','workshop','accountant'])
def test_api_permission_boundaries(context, role):
    client, headers, _, _ = context
    for path in ('users','operation-logs','settings/expiry'):
        assert client.get('/api/'+path, headers=headers[role]).status_code == 200
        assert client.get('/api/'+path).status_code == 401
    assert client.put('/api/settings/expiry', headers=headers[role], json={'urgent_expiry_days':30,'warning_expiry_days':90}).status_code == 403
    for path in ('dashboard/summary','dashboard/recent-transactions','dashboard/recent-production-orders','alerts/expiry'):
        assert client.get('/api/'+path, headers=headers[role]).status_code == 200
        assert client.get('/api/'+path).status_code == 401
        assert client.get('/api/'+path, headers={'Authorization':'Bearer invalid'}).status_code == 401
    assert client.delete('/api/operation-logs/1', headers=headers[role]).status_code in (404,405)


def test_workshop_registration_and_complete_audit(context):
    data = scenario(context, 'workshop', stock_batches=((0,'30',60),))
    row = data['order']['materials'][0]
    args = (row['id'], [{'batch_id':data['batches'][0], 'quantity':'20'}])
    assert post_material(data, 'issue', *args, role='workshop').status_code == 200
    for kind, quantity in [('supplement','1.2'),('return','0.5')]:
        assert post_material(data, kind, row['id'], [{'batch_id':data['batches'][0], 'quantity':quantity}], role='workshop').status_code == 200
    client, headers, _, _ = context
    path = f"/api/production-orders/{data['order']['id']}/actual-quantity"
    assert client.put(path, headers=headers['workshop'], json={'actual_quantity':'980'}).status_code == 403
    response = client.put(path, headers=headers['workshop_director'], json={'actual_quantity':'980'})
    assert response.status_code == 200 and response.json()['data']['actual_quantity'] == '980'
    assert detail(data)['status'] == 'in_progress'
    assert settle(data, role='workshop').status_code == 403
    completed = settle(data)
    assert completed.status_code == 200
    assert completed.json()['data']['materials'][0]['loss_quantity'] == '1.1'
    assert client.put(path, headers=headers['workshop_director'], json={'actual_quantity':'1000'}).status_code == 409
    for action in ('material_receipt','production_issue','production_supplement','production_return','register_actual_quantity','complete_production_order'):
        result = get(context, 'operation-logs', action=action, q=str(data['order']['id']) if action in ('complete_production_order','register_actual_quantity') else '')
        assert result['total'] > 0
        assert all(r['username_snapshot'] and r['description'] for r in result['items'])
    assert get(context, 'operation-logs', action='login')['total'] > 0


def test_dashboard_matches_database_and_postings(context):
    before = get(context, 'dashboard/summary')
    data = scenario(context, 'dashboard', stock_batches=((0,'30',30),))
    row = data['order']['materials'][0]
    post_material(data, 'issue', row['id'], [{'batch_id':data['batches'][0], 'quantity':'20'}])
    post_material(data, 'return', row['id'], [{'batch_id':data['batches'][0], 'quantity':'1'}])
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as s:
        s.get(RawMaterial, data['materials'][0]['id']).min_stock = Decimal('12')
        s.commit()
    current = get(context, 'dashboard/summary')
    for key in ('materials','products','stocked_batches','in_progress_orders','low_stock_materials','urgent_batches','warning_batches'):
        assert current[key] == before[key] + 1, key
    def quantity(snapshot, key, unit):
        return sum(Decimal(r['quantity']) for r in snapshot['today'][key] if r['unit']==unit)
    assert quantity(current,'material_in','kg') - quantity(before,'material_in','kg') == 31
    assert quantity(current,'material_out','kg') - quantity(before,'material_out','kg') == 20
    assert settle(data).status_code == 200
    receipt = get(context, 'dashboard/summary')
    assert receipt['in_progress_orders'] == before['in_progress_orders']
    assert quantity(receipt,'finished_in','盒') - quantity(before,'finished_in','盒') == 980
    from uuid import uuid4
    finished_batch = context[0].get(f"/api/finished-stock/{data['product']['id']}/batches", headers=context[1]['warehouse']).json()['data'][0]
    response = context[0].post('/api/finished-outbounds', headers=context[1]['warehouse'], json={
        'request_id':uuid4().hex,'product_id':data['product']['id'],'quantity':'10','unit':'盒','business_date':str(business_today()),
        'allocations':[{'batch_id':finished_batch['id'],'quantity':'10'}]})
    assert response.status_code == 200
    assert quantity(get(context,'dashboard/summary'),'finished_out','盒') - quantity(before,'finished_out','盒') == 10
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as s:
        assert receipt['materials'] == s.scalar(select(func.count()).select_from(RawMaterial).where(RawMaterial.deleted_at.is_(None)))
        assert receipt['products'] == s.scalar(select(func.count()).select_from(FinishedProduct).where(FinishedProduct.deleted_at.is_(None)))
        assert receipt['expired_batches'] == s.scalar(select(func.count()).select_from(MaterialBatch).where(MaterialBatch.remaining_quantity>0,MaterialBatch.expiry_date<business_today()))
    transactions = get(context,'dashboard/recent-transactions')['items']
    assert len(transactions) <= 10 and transactions[0]['kind'] == 'finished_outbound'
    assert transactions[0]['name'] == data['product']['name'] and transactions[0]['operator']
    assert get(context,'dashboard/recent-production-orders')['items'][0]['id'] == data['order']['id']


def test_business_timezone_and_today_boundary(context):
    import app.overview as overview
    from unittest.mock import patch
    fixed = datetime(2026,9,20,tzinfo=timezone.utc).date()
    start,end = date_bounds(fixed,fixed)
    assert start == datetime(2026,9,19,16,tzinfo=timezone.utc)
    assert end == datetime(2026,9,20,16,tzinfo=timezone.utc)
    with patch.object(overview,'business_today',return_value=fixed):
        baseline = get(context,'dashboard/summary')
        moments = iter([start-timedelta(seconds=1),start,end-timedelta(seconds=1),end])
        def stamp(session, *args):
            for row in session.new:
                if isinstance(row,InventoryTransaction) and row.kind == 'material_receipt':
                    row.created_at = next(moments)
        event.listen(Session,'before_flush',stamp)
        try:
            scenario(context,'midnight',stock_batches=((0,'1',100),(0,'2',100),(0,'4',100),(0,'8',100)))
        finally:
            event.remove(Session,'before_flush',stamp)
        current = get(context,'dashboard/summary')
        def kg(snapshot):
            return sum(Decimal(row['quantity']) for row in snapshot['today']['material_in'] if row['unit']=='kg')
        assert kg(current)-kg(baseline)==6
        assert current['business_date']==str(fixed)


def test_settings_audit_failure_rolls_back(context):
    before = get(context,'settings/expiry')
    def fail(session, *args):
        if any(isinstance(row,OperationLog) and row.action=='update_settings' for row in session.new):
            raise RuntimeError('forced audit failure')
    event.listen(Session,'before_flush',fail)
    try:
        r=context[0].put('/api/settings/expiry',headers=context[1]['admin'],json={'urgent_expiry_days':40,'warning_expiry_days':100})
        assert r.status_code==500
    finally:
        event.remove(Session,'before_flush',fail)
    assert get(context,'settings/expiry')==before


def test_audit_filters_and_snapshot(context):
    client,headers,connection,_=context
    current = client.get('/api/auth/me',headers=headers['admin']).json()['data']
    rows=get(context,'operation-logs',username=current['username'],module='users',action='login',date_from=str(business_today()),date_to=str(business_today()))
    assert rows['total']>=1
    assert get(context,'operation-logs',username=current['username'],page=10000)['items']==[]
    assert get(context,'operation-logs',q='%')['total']==0
    assert client.get('/api/operation-logs',headers=headers['admin'],params={'date_from':'2026-09-20','date_to':'2026-09-19'}).status_code==422
    with Session(bind=connection,join_transaction_mode='create_savepoint') as s:
        s.get(User,current['id']).display_name='已改显示名称'
        s.commit()
    assert get(context,'operation-logs',username=current['username'],action='login')['items'][0]['username_snapshot']==current['username']
