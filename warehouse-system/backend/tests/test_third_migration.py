"""Guarded MySQL 0013 round-trip preserving each legacy fact row."""
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, select, text, inspect
from app.database import engine, SessionLocal
from app.reconciliation import reconcile
from app.stock_service import business_today
from test_phase7_concurrency import committed_production
from test_phase7 import setup_order, settle
from test_traceability import ok
from test_reversals import reverse
from scripts.check_test_target import check_test_target
from uuid import uuid4

pytestmark = pytest.mark.mysql


def history():
    metadata = MetaData()
    metadata.reflect(engine)
    with engine.connect() as connection:
        return {name: list(connection.execute(select(*[c for c in table.columns if not c.computed]).order_by(*table.primary_key.columns)))
                for name, table in metadata.tables.items() if name != 'alembic_version'}


def version():
    with engine.connect() as connection:
        return connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()


def test_0013_roundtrip_with_legacy_stock_and_all_fact_rows(committed_production):
    check_test_target()
    data = setup_order(committed_production, 'migration14')
    client, headers = data['client'], data['headers']
    for path, quantity in [('finished-receipts', '10.123456'), ('finished-outbounds', '2.000001')]:
        ok(client.post('/api/' + path, headers=headers['warehouse'], json={
            'request_id': uuid4().hex, 'product_id': data['product']['id'], 'quantity': quantity,
            'unit': '盒', 'business_date': str(business_today())}))
    config = Config('alembic.ini')
    before = history()
    try:
        command.downgrade(config, '0013')
        assert version() == '0013' and history() == before
        command.upgrade(config, '0014')
        assert version() == '0014' and history() == before
        command.current(config); command.heads(config); command.check(config)
        command.downgrade(config, '0013')
        assert version() == '0013' and history() == before
        command.upgrade(config, 'head')
        assert history() == before
        ok(settle(data, '1000', finished_batches=[{'batch_no': 'MIG-FG1', 'quantity': '600'}, {'batch_no': 'MIG-FG2', 'quantity': '400'}]))
        after = history()
        with pytest.raises(RuntimeError, match='immutable facts'):
            command.downgrade(config, '0013')
        assert version() == '0014' and history() == after
        command.check(config)
        with SessionLocal() as session:
            assert reconcile(session)['problems'] == []
    finally:
        command.upgrade(config, 'head')


def test_material_reversal_alone_protects_downgrade(committed_production):
    check_test_target()
    data = setup_order(committed_production, 'guard14')
    with SessionLocal() as session:
        from app.models import MaterialIssueRecord
        original = session.scalar(select(MaterialIssueRecord.document_id).where(
            MaterialIssueRecord.order_material_id == data['order']['materials'][0]['id']))
    ok(reverse(data['client'], data['headers'], original))
    before = history()
    with pytest.raises(RuntimeError, match='immutable facts'):
        command.downgrade(Config('alembic.ini'), '0013')
    assert version() == '0014' and history() == before
