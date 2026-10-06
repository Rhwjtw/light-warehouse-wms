"""Partial production issues and explicit material dispositions on isolated MySQL."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from test_phase6 import context, scenario, post_material, detail
from app import stock_service, production_service, stock
from app.models import StockDocument, InventoryTransaction, MaterialBatch, MaterialMovement, OperationLog
from app.reconciliation import reconcile

pytestmark = pytest.mark.mysql


def outbound(data, purpose='normal', quantity='2', request_id=None, notes=None, role='warehouse'):
    return data['client'].post('/api/material-outbounds', headers=data['headers'][role], json={
        'request_id': request_id or uuid4().hex, 'material_id': data['materials'][0]['id'],
        'batch_id': data['batches'][0], 'quantity': quantity, 'unit': 'kg',
        'purpose': purpose, 'notes': notes,
    })


def test_10000_boxes_issue_2000_5000_3000_in_separate_visits(context):
    data = scenario(context, 'v13partial', stock_batches=((0, '250', 30),))
    response = data['client'].post('/api/production-orders', headers=data['headers']['admin'], json={
        'finished_product_id': data['product']['id'], 'planned_quantity': '10000'})
    assert response.status_code == 200, response.text
    data['order'] = response.json()['data']
    material = data['order']['materials'][0]
    assert material['planned_required_quantity'] == '200'
    for amount, cumulative in [('40', '40'), ('100', '140'), ('60', '200')]:
        key = uuid4().hex
        result = post_material(data, 'issue', material['id'], [{'batch_id': data['batches'][0], 'quantity': amount}], role='workshop', request_id=key)
        assert result.status_code == 200, result.text
        replay = post_material(data, 'issue', material['id'], [{'batch_id': data['batches'][0], 'quantity': amount}], role='workshop', request_id=key)
        assert replay.json()['data'] == result.json()['data']
        current = detail(data)
        assert current['materials'][0]['issued_quantity'] == cumulative
        assert current['materials'][0]['net_issued_quantity'] == cumulative
    assert len(current['material_records']) == 3
    assert current['status'] == 'in_progress'
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        assert session.get(MaterialBatch, data['batches'][0]).remaining_quantity == Decimal('50')
        assert reconcile(session)['problems'] == []


@pytest.mark.parametrize('purpose', ['supplier_return', 'scrap'])
def test_expired_disposition_requires_reason_preserves_ledger_and_reverses(context, monkeypatch, purpose):
    data = scenario(context, 'v13'+purpose, stock_batches=((0, '30', 2),))
    future = stock_service.business_today() + timedelta(days=3)
    monkeypatch.setattr(stock_service, 'business_today', lambda: future)
    monkeypatch.setattr(stock, 'business_today', lambda: future)
    monkeypatch.setattr(production_service, 'business_today', lambda: future)
    client, h = data['client'], data['headers']
    assert client.get('/api/material-fefo', headers=h['warehouse'], params={'material_id':data['materials'][0]['id']}).json()['data']['total'] == 0
    candidates = client.get('/api/material-fefo', headers=h['warehouse'], params={'material_id':data['materials'][0]['id'], 'purpose':purpose}).json()['data']
    assert candidates['total'] == 1 and candidates['items'][0]['expired']
    assert candidates['items'][0]['supplier_name'] == data['supplier']['name']
    assert outbound(data).status_code == 422
    assert outbound(data, purpose, notes='   ').status_code == 422
    assert post_material(data, 'issue', data['order']['materials'][0]['id'], [{'batch_id':data['batches'][0], 'quantity':'1'}]).status_code == 422
    for role in ('workshop', 'workshop_director', 'accountant'):
        assert outbound(data, purpose, notes='原因', role=role).status_code == 403
    key = uuid4().hex
    response = outbound(data, purpose, quantity='2.000001', notes='过期原料处置', request_id=key)
    assert response.status_code == 200, response.text
    record = response.json()['data']
    assert record['purpose'] == purpose
    assert Decimal(record['after_quantity']) == Decimal('27.999999')
    assert outbound(data, purpose, quantity='2.000001', notes='过期原料处置', request_id=key).json()['data'] == record
    assert outbound(data, 'scrap' if purpose == 'supplier_return' else 'supplier_return', quantity='2.000001', notes='过期原料处置', request_id=key).status_code == 409
    assert outbound(data, purpose, quantity='28', notes='超量').status_code == 409
    tx = client.get('/api/material-transactions', headers=h['warehouse'], params={'batch_id':data['batches'][0], 'kind':'material_outbound'}).json()['data']['items']
    assert len(tx) == 1 and tx[0]['material_outbound_purpose'] == purpose
    info = client.get(f"/api/stock-documents/{record['id']}", headers=h['warehouse']).json()['data']
    assert info['material_outbound_purpose'] == purpose and info['notes'] == '过期原料处置'
    reversal = client.post(f"/api/stock-documents/{record['id']}/reverse", headers=h['warehouse'], json={'request_id':uuid4().hex, 'reason':'录入错误，恢复原批次'})
    assert reversal.status_code == 200, reversal.text
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        assert session.get(MaterialBatch, data['batches'][0]).remaining_quantity == Decimal('30')
        assert reconcile(session)['problems'] == []


def test_disposition_failure_rolls_back_document_stock_details_and_ledger(context, monkeypatch):
    data = scenario(context, 'v13rollback', stock_batches=((0, '30', 30),))
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        before = {m: session.scalar(select(func.count()).select_from(m)) for m in (StockDocument, MaterialMovement, InventoryTransaction, OperationLog)}
    def fail(*args, **kwargs):
        raise RuntimeError('isolated audit failure')
    monkeypatch.setattr(stock_service, 'audit', fail)
    response = outbound(data, 'scrap', notes='破损报废')
    assert response.status_code == 500
    with Session(bind=context[2], join_transaction_mode='create_savepoint') as session:
        assert session.get(MaterialBatch, data['batches'][0]).remaining_quantity == Decimal('30')
        assert {m: session.scalar(select(func.count()).select_from(m)) for m in before} == before
