"""Real committed writes and independent connections at the unfinished/completed boundary."""
from uuid import uuid4
from sqlalchemy import select
from app.models import MaterialIssueRecord, User
from app.schemas import ProductionCompleteInput, FinishedStockInput, FinishedAllocationInput
from app import settlement_service, finished_service
from app.database import SessionLocal
from test_phase7_concurrency import committed_production
from test_phase7 import setup_order
from test_second_reversals import run_race, reversal
from test_traceability import trace, batches
from app.stock_service import business_today


def test_completion_and_issue_reversal_serialize(committed_production):
    ctx = committed_production
    data = setup_order(ctx, 'race14')
    with SessionLocal() as session:
        document_id = session.scalar(select(MaterialIssueRecord.document_id).where(
            MaterialIssueRecord.order_material_id == data['order']['materials'][0]['id']))
    complete = lambda s, u: settlement_service.complete(s, u, data['order']['id'], ProductionCompleteInput(actual_quantity='1000'))
    results = run_race(ctx, [complete, reversal(document_id)])
    assert sorted(r[1] for r in results) == [200, 409]
    result = trace(data, 'material-batches', data['batches'][0], role='admin')
    if results[0][1] == 200:
        assert result['production_orders'][0]['actual_quantity'] == '1000.000000'
    else:
        assert result['production_orders'] == []


def test_batch_outbound_race_never_overdraws(committed_production):
    ctx = committed_production
    data = setup_order(ctx, 'fg-race14')
    with SessionLocal.begin() as session:
        user = session.scalar(select(User).where(User.username == ctx[3]))
        settlement_service.complete(session, user, data['order']['id'], ProductionCompleteInput(actual_quantity='1000'))
    stock = batches(data)[0]
    def debit(s, u):
        return finished_service.post_finished(s, u, FinishedStockInput(request_id=uuid4().hex,
            product_id=data['product']['id'], quantity='600', unit='盒', business_date=business_today(),
            allocations=[FinishedAllocationInput(batch_id=stock['id'], quantity='600')]), 'finished_outbound')
    results = run_race(ctx, [debit, debit])
    assert sorted(r[1] for r in results) == [200, 409]
    result = trace(data, 'finished-batches', stock['id'], role='admin')
    assert result['subject']['current_quantity'] == '400.000000'
    assert result['production_orders'][0]['materials'][0]['net_used_quantity'] == '20.000000'
