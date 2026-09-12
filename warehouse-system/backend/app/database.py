from datetime import datetime, timezone

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import DateTime, TypeDecorator

from app.config import get_settings


class UTCDateTime(TypeDecorator):
    """MySQL DATETIME 存 UTC；Python/API 使用带时区的 datetime。"""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError('时间必须包含时区')
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        'ix': 'ix_%(table_name)s_%(column_0_name)s',
        'uq': 'uq_%(table_name)s_%(column_0_name)s',
        'ck': 'ck_%(table_name)s_%(constraint_name)s',
        'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
        'pk': 'pk_%(table_name)s',
    })


engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_recycle=1800,
                       connect_args={'connect_timeout': 5}, isolation_level='READ COMMITTED')


@event.listens_for(engine, 'connect')
def set_mysql_timezone(dbapi_connection, connection_record):
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SET time_zone = '+00:00'")


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session():
    # 一个请求一个事务：任意异常自动回滚，正常结束才提交。
    with SessionLocal.begin() as session:
        yield session
