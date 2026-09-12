from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import Base, SessionLocal, UTCDateTime, engine
from app.main import app
from app.models import Location, OperationLog, RawMaterial, Supplier, Unit, MaterialBatch


def test_health_and_openapi():
    with TestClient(app) as client:
        response = client.get('/api/health')
        assert response.status_code == 200
        assert response.json()['data']['time'].endswith('+00:00')
        assert 'x-request-id' in response.headers
        assert client.get('/docs').status_code == 200
        assert '/api/health/db' in client.get('/openapi.json').json()['paths']
        missing = client.get('/api/missing')
        assert missing.status_code == 404
        assert missing.json()['code'] == 'HTTP_ERROR'


def test_timezone_roundtrip():
    column = UTCDateTime()
    local = datetime(2026, 9, 12, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    stored = column.process_bind_param(local, None)
    assert stored == datetime(2026, 9, 12, 12, 0)
    assert column.process_result_value(stored, None).tzinfo == timezone.utc
    with pytest.raises(ValueError):
        column.process_bind_param(datetime(2026, 9, 12), None)


@pytest.mark.mysql
def test_mysql_schema_and_health():
    inspector = inspect(engine)
    assert set(Base.metadata.tables).issubset(set(inspector.get_table_names()))
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0002'
        assert connection.execute(text('SELECT @@session.time_zone')).scalar_one() == '+00:00'
        assert connection.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND engine <> 'InnoDB'")).scalar_one() == 0
    with TestClient(app) as client:
        assert client.get('/api/health/db').status_code == 200


@pytest.mark.mysql
def test_real_mysql_transaction_rollback():
    code = 'rollback_' + uuid4().hex[:12]
    with pytest.raises(RuntimeError):
        with SessionLocal.begin() as session:
            session.add(Supplier(code=code, name='回滚测试'))
            session.flush()
            raise RuntimeError('模拟记账中断')
    with SessionLocal() as session:
        assert session.scalar(select(Supplier).where(Supplier.code == code)) is None


@pytest.mark.mysql
def test_decimal_stock_constraints_and_foreign_keys():
    token = uuid4().hex[:12]
    with SessionLocal() as session:
        # 整个测试最终回滚，不污染用户的业务数据。
        with session.begin():
            supplier = Supplier(code=token, name='测试供应商')
            location = Location(code=token, name='测试库位')
            material = RawMaterial(code=token, name='精度测试', unit='kg')
            session.add_all([supplier, location, material])
            session.flush()
            batch = MaterialBatch(material_id=material.id, batch_no=token, received_quantity=Decimal('20.700001'),
                                  remaining_quantity=Decimal('20.700001'), unit='kg',
                                  production_date=datetime(2026, 1, 1).date(), expiry_date=datetime(2027, 1, 1).date(),
                                  supplier_id=supplier.id, location_id=location.id)
            session.add(batch)
            session.flush()
            session.refresh(batch)
            assert batch.remaining_quantity == Decimal('20.700001')
            with pytest.raises(DBAPIError) as rejected:
                with session.begin_nested():
                    batch.remaining_quantity = Decimal('-0.000001')
                    session.flush()
            assert rejected.value.orig.args[0] == 3819
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(RawMaterial(code=token+'x', name='无效单位', unit='missing'))
                    session.flush()
            session.rollback()


@pytest.mark.mysql
def test_reference_units_and_warning_rules():
    with SessionLocal() as session:
        grams = session.get(Unit, 'g')
        kilograms = session.get(Unit, 'kg')
        assert grams.dimension == kilograms.dimension
        assert Decimal('20') * grams.factor / kilograms.factor * Decimal('980') == Decimal('19.6')
        assert session.get(Unit, 'L').dimension != grams.dimension
        assert session.get(Unit, '盒').dimension != session.get(Unit, '个').dimension
        assert session.execute(text('SELECT days FROM warning_rules ORDER BY days')).scalars().all() == [30, 90]


@pytest.mark.mysql
def test_append_only_logs_and_no_ddl_permission():
    with SessionLocal() as session:
        log = OperationLog(action='test', entity_type='test')
        session.add(log)
        session.flush()
        for sql in ('UPDATE operation_logs SET action = :action WHERE id = :id',
                    'DELETE FROM operation_logs WHERE id = :id'):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(text(sql), {'id': log.id, 'action': 'modified'})
        session.rollback()
    with engine.connect() as connection:
        grants = ' '.join(row[0] for row in connection.execute(text('SHOW GRANTS')))
        assert 'CREATE' not in grants and 'DROP' not in grants and 'ALL PRIVILEGES' not in grants


def test_database_failure_returns_503_without_secrets(monkeypatch):
    def failed_connect():
        raise DBAPIError('secret statement', {}, RuntimeError('secret password'))
    monkeypatch.setattr(engine, 'connect', failed_connect)
    with TestClient(app) as client:
        response = client.get('/api/health/db')
        assert response.status_code == 503
        assert response.json()['code'] == 'DATABASE_NOT_READY'
        assert 'secret' not in response.text
