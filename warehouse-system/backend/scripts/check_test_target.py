"""Refuse database tests unless both accounts point to one isolated MySQL instance."""
import os
from pathlib import Path

import pymysql

from app.config import get_settings


def check_test_target() -> None:
    settings = get_settings()
    expected_uuid = os.environ.get('WMS_TEST_SERVER_UUID', '')
    expected_datadir = os.environ.get('WMS_TEST_DATADIR', '')
    expected_port = os.environ.get('WMS_TEST_PORT', '')
    if not all((expected_uuid, expected_datadir, expected_port)):
        raise RuntimeError('缺少隔离测试实例标识，拒绝连接数据库执行测试')
    if (settings.app_env != 'development' or settings.mysql_host != '127.0.0.1'
            or settings.mysql_port == 3306 or str(settings.mysql_port) != expected_port
            or settings.mysql_database != 'warehouse_system'
            or not settings.mysql_user.startswith('wms_e2e_')
            or not settings.mysql_migration_user.startswith('wms_e2e_')
            or settings.mysql_user == settings.mysql_migration_user):
        raise RuntimeError('配置不是独立测试实例，拒绝连接数据库执行测试')
    for user, password in (
        (settings.mysql_user, settings.mysql_password.get_secret_value()),
        (settings.mysql_migration_user, settings.mysql_migration_password.get_secret_value()),
    ):
        with pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=user,
                             password=password, database=settings.mysql_database,
                             connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT @@server_uuid, @@datadir, @@port, DATABASE()')
                server_uuid, datadir, port, database = cursor.fetchone()
        if (server_uuid != expected_uuid or Path(datadir).resolve() != Path(expected_datadir).resolve()
                or port != settings.mysql_port or database != settings.mysql_database):
            raise RuntimeError('测试实例身份不匹配，拒绝连接数据库执行测试')


if __name__ == '__main__':
    check_test_target()
    print('隔离测试实例校验通过')
