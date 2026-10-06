"""0014 historical facts survive 0015; disposition facts prevent downgrade."""
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, select, text
import pytest

from app.database import engine, SessionLocal
from app.reconciliation import reconcile
from scripts.check_test_target import check_test_target
from test_phase7_concurrency import committed_production
from test_phase6 import scenario
from test_v13_material_operations import outbound

pytestmark = pytest.mark.mysql


def facts():
    metadata = MetaData()
    metadata.reflect(engine)
    with engine.connect() as connection:
        return {name: list(connection.execute(select(*[c for c in table.columns
                    if not c.computed and c.name != 'material_outbound_purpose']).order_by(*table.primary_key.columns)))
                for name, table in metadata.tables.items() if name != 'alembic_version'}


def test_0015_upgrade_preserves_history_and_guards_disposition_downgrade(committed_production):
    check_test_target()
    config = Config('alembic.ini')
    data = scenario(committed_production, 'v13migration', stock_batches=((0,'30',30),))
    ordinary = outbound(data)
    assert ordinary.status_code == 200, ordinary.text
    try:
        before = facts()
        command.downgrade(config, '0014')
        assert facts() == before
        command.upgrade(config, 'head')
        assert facts() == before
        with engine.connect() as connection:
            assert connection.execute(text('SELECT material_outbound_purpose FROM stock_documents WHERE id=:id'),
                    {'id':ordinary.json()['data']['id']}).scalar_one() is None
        command.check(config)
        returned = outbound(data, 'supplier_return', notes='验收：原供应商退货')
        assert returned.status_code == 200, returned.text
        after = facts()
        with pytest.raises(RuntimeError, match='immutable facts'):
            command.downgrade(config, '0014')
        assert facts() == after
        with engine.connect() as connection:
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0015'
            assert connection.execute(text('SELECT material_outbound_purpose FROM stock_documents WHERE id=:id'),
                    {'id':returned.json()['data']['id']}).scalar_one() == 'supplier_return'
        with SessionLocal() as session:
            assert reconcile(session)['problems'] == []
    finally:
        command.upgrade(config, 'head')
