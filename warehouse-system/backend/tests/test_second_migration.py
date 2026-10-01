"""DDL rehearsal with committed first-batch history on guarded isolated MySQL only."""
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, select, text, MetaData
from app.database import SessionLocal, Base, engine
from scripts.check_test_target import check_test_target
from test_phase7_concurrency import committed_production  # noqa: F401
from test_second_reversals import committed_inventory, setup, data, post
from test_phase3 import out
from test_reversals import reverse
from app.reconciliation import reconcile

pytestmark=pytest.mark.mysql


def history():
    metadata = MetaData()
    metadata.reflect(bind=engine)
    with engine.connect() as connection:
        return {name:list(connection.execute(select(table).order_by(*table.primary_key.columns)))
                for name,table in metadata.tables.items() if name != 'alembic_version'}


def structure():
    inspector=inspect(engine)
    return {name:(str(inspector.get_columns(name)),str(inspector.get_indexes(name)),
                  str(inspector.get_foreign_keys(name))) for name in inspector.get_table_names()}


def version():
    with engine.connect() as connection: return connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()


def test_0012_history_upgrade_downgrade_and_guard(committed_production):
    check_test_target()
    config=Config('alembic.ini')
    try:
        inv=committed_inventory(committed_production)
        put=setup(inv,'putaway')
        outgoing=data(out(inv,put['target_batch_id']))
        data(reverse(inv[0],inv[1],outgoing['id']))
        finished=setup(inv,'finished_receipt')
        finished_out=data(post(inv,'finished-outbounds',product_id=finished['product_id'],quantity='2',unit='盒'))
        data(reverse(inv[0],inv[1],finished_out['id']))
        # Create facts through the current service before downgrading; the 0014 ORM
        # must never be run against an older schema lacking its generated columns.
        command.downgrade(config,'0012')
        assert version()=='0012'
        before,shape=history(),structure()
        command.upgrade(config,'0013')
        assert version()=='0013' and before==history() and shape==structure()
        command.current(config); command.heads(config)
        checks=inspect(engine).get_check_constraints('stock_documents')
        valid=next(row['sqltext'] for row in checks if row['name']=='ck_stock_documents_valid_reversal')
        for kind in ('material_receipt','putaway','transfer','finished_receipt','adjustment'):
            assert kind in valid
        # Without new-kind reversal history downgrade is lossless and permitted.
        command.downgrade(config,'0012')
        assert version()=='0012' and before==history()
        command.upgrade(config,'head')
        data(reverse(inv[0],inv[1],finished['id']))
        command.downgrade(config,'0013')
        after=history()
        with pytest.raises(RuntimeError,match='immutable facts'):
            command.downgrade(config,'0012')
        assert version()=='0013' and after==history()
    finally:
        command.upgrade(config,'head')
        # Schema drift is compared with current model metadata only at current head.
        command.check(config)
        with SessionLocal() as session: assert reconcile(session)['problems']==[]
