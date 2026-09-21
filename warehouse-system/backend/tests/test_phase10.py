from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings
from app.database import SessionLocal
from app.models import User
from scripts.init_admin import ensure_initial_admin


def production_settings(**overrides):
    values = {
        'app_env': 'production',
        'mysql_user': 'warehouse_app',
        'mysql_password': 'app-secret',
        'mysql_migration_user': 'warehouse_migrator',
        'mysql_migration_password': 'migration-secret',
        'auth_secret': 'a' * 40,
        'cors_origins': [],
        'serve_frontend': True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_configuration_rejects_insecure_values():
    assert production_settings().app_env == 'production'
    for change in (
        {'auth_secret': 'short'},
        {'mysql_user': 'root'},
        {'mysql_migration_user': 'warehouse_app'},
        {'cors_origins': ['*']},
    ):
        with pytest.raises(ValidationError):
            production_settings(**change)


def test_initial_admin_is_idempotent_and_does_not_reset_password():
    username = 'phase10_' + uuid4().hex[:12]
    with SessionLocal() as session:
        transaction = session.begin()
        try:
            assert ensure_initial_admin(session, username=username, display_name='部署测试管理员',
                                        password='Strong-Initial-Password!') is True
            first_hash = session.scalar(select(User.password_hash).where(User.username == username))
            assert ensure_initial_admin(session, username=username, display_name='不会覆盖',
                                        password='Another-Strong-Password!') is False
            second_hash = session.scalar(select(User.password_hash).where(User.username == username))
            assert second_hash == first_hash
        finally:
            transaction.rollback()
