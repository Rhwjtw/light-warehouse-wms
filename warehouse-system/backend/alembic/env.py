from alembic import context
from sqlalchemy import create_engine
from app.database import Base
from app import models  # noqa: F401
from app.config import get_settings

config = context.config
target_metadata = Base.metadata
settings = get_settings()
migration_url = settings.database_url.set(username=settings.mysql_migration_user,
                                          password=settings.mysql_migration_password.get_secret_value())


def render_item(type_, obj, autogen_context):
    if type_ == 'type' and obj.__class__.__name__ == 'UTCDateTime':
        # 迁移固定为数据库类型，不依赖以后可能变化的应用实现。
        return 'sa.DateTime()'
    return False


if context.is_offline_mode():
    context.configure(url=migration_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(migration_url, connect_args={'connect_timeout': 5})
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, render_item=render_item)
        with context.begin_transaction():
            context.run_migrations()
