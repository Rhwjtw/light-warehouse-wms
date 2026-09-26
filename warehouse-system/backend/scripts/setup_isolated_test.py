"""Initialize only a newly created local MySQL instance inside .runtime."""
import json
import os
import secrets
import sys
from pathlib import Path

import pymysql
from dotenv import dotenv_values, set_key


APP_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = (APP_ROOT / '.runtime').resolve()
DATABASE = 'warehouse_system'
APP_USER = 'wms_e2e_app'
MIGRATION_USER = 'wms_e2e_migrator'


def target():
    run_root = Path(sys.argv[2]).resolve()
    port = int(sys.argv[3])
    if run_root.parent != RUNTIME or not run_root.name.startswith('e2e-'):
        raise RuntimeError('隔离实例只能位于本项目 .runtime/e2e-* 目录')
    if not 1024 <= port <= 65535 or port == 3306:
        raise RuntimeError('隔离实例必须使用独立的非正式数据库端口')
    data = (run_root / 'mysql-data').resolve()
    if not data.is_dir() or data.parent != run_root:
        raise RuntimeError('隔离 MySQL 数据目录不存在或不在目标目录内')
    return run_root, data, port


def verify(connection, run_root, data, port):
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@port, @@datadir, @@server_uuid, DATABASE()')
        actual_port, datadir, server_uuid, database = cursor.fetchone()
    if actual_port != port or Path(datadir).resolve() != data:
        raise RuntimeError('实例端口或数据目录不匹配；拒绝写入')
    identity_path = run_root / 'identity.json'
    if identity_path.exists():
        expected = json.loads(identity_path.read_text(encoding='utf-8'))
        if server_uuid != expected['server_uuid']:
            raise RuntimeError('实例 UUID 不匹配；拒绝写入')
    return server_uuid, database


def create(run_root, data, port):
    env_path = run_root / 'source' / 'backend' / '.env'
    if env_path.exists() or not (run_root / 'source' / 'backend' / 'alembic.ini').is_file():
        raise RuntimeError('隔离源码不完整或 .env 已存在；拒绝覆盖')
    app_password = secrets.token_urlsafe(32)
    migration_password = secrets.token_urlsafe(32)
    root_password = secrets.token_urlsafe(32)
    with pymysql.connect(host='127.0.0.1', port=port, user='root', password='',
                         autocommit=True, connect_timeout=5) as connection:
        server_uuid, database = verify(connection, run_root, data, port)
        if database is not None:
            raise RuntimeError('初始化连接不得预先选中数据库')
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE `{DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            cursor.execute("CREATE USER %s@'127.0.0.1' IDENTIFIED BY %s", (APP_USER, app_password))
            cursor.execute("CREATE USER %s@'127.0.0.1' IDENTIFIED BY %s", (MIGRATION_USER, migration_password))
            cursor.execute(f"GRANT SELECT ON `{DATABASE}`.* TO '{APP_USER}'@'127.0.0.1'")
            cursor.execute(f"GRANT ALL PRIVILEGES ON `{DATABASE}`.* TO '{MIGRATION_USER}'@'127.0.0.1' WITH GRANT OPTION")
            cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (root_password,))
    (run_root / 'identity.json').write_text(json.dumps({
        'server_uuid': server_uuid, 'port': port, 'datadir': str(data), 'database': DATABASE,
    }, indent=2), encoding='utf-8')
    env_path.touch()
    values = {
        'APP_ENV': 'development', 'MYSQL_HOST': '127.0.0.1', 'MYSQL_PORT': str(port),
        'MYSQL_DATABASE': DATABASE, 'MYSQL_USER': APP_USER, 'MYSQL_PASSWORD': app_password,
        'MYSQL_MIGRATION_USER': MIGRATION_USER, 'MYSQL_MIGRATION_PASSWORD': migration_password,
        'AUTH_SECRET': secrets.token_urlsafe(48), 'CORS_ORIGINS': '[]',
        'API_DOCS_ENABLED': 'true', 'SERVE_FRONTEND': 'true',
        'FRONTEND_DIST': str((run_root / 'source' / 'frontend' / 'dist').resolve()),
    }
    for key, value in values.items():
        set_key(str(env_path), key, value)
    print('隔离测试库与专用账号已创建；临时 root 密码未保存')


def preflight(run_root, data, port):
    values = dotenv_values(run_root / 'source' / 'backend' / '.env')
    expected = {'APP_ENV': 'development', 'MYSQL_HOST': '127.0.0.1',
                'MYSQL_PORT': str(port), 'MYSQL_DATABASE': DATABASE,
                'MYSQL_USER': APP_USER, 'MYSQL_MIGRATION_USER': MIGRATION_USER}
    for key, value in expected.items():
        if values.get(key) != value or key in os.environ:
            raise RuntimeError(f'隔离测试配置 {key} 不匹配或存在进程覆盖')
    for key in ('MYSQL_PASSWORD', 'MYSQL_MIGRATION_PASSWORD', 'AUTH_SECRET'):
        if key in os.environ:
            raise RuntimeError(f'进程配置覆盖 {key}，拒绝运行')
    with pymysql.connect(host='127.0.0.1', port=port, user=MIGRATION_USER,
                         password=values['MYSQL_MIGRATION_PASSWORD'], database=DATABASE,
                         connect_timeout=5) as connection:
        _, database = verify(connection, run_root, data, port)
        if database != DATABASE:
            raise RuntimeError('隔离测试库名不匹配')
    print('隔离实例身份与配置核对通过')


def grants(run_root, data, port):
    preflight(run_root, data, port)
    values = dotenv_values(run_root / 'source' / 'backend' / '.env')
    with pymysql.connect(host='127.0.0.1', port=port, user=MIGRATION_USER,
                         password=values['MYSQL_MIGRATION_PASSWORD'], database=DATABASE,
                         autocommit=True, connect_timeout=5) as connection:
        _, database = verify(connection, run_root, data, port)
        if database != DATABASE:
            raise RuntimeError('隔离测试库名不匹配；拒绝授权')
        with connection.cursor() as cursor:
            cursor.execute('SHOW TABLES')
            tables = [row[0] for row in cursor.fetchall()]
            legacy = len(tables) == 24 and not {'material_transfers', 'material_counts', 'material_adjustments'}.intersection(tables)
            current = len(tables) == 27 and {'material_transfers', 'material_counts', 'material_adjustments'}.issubset(tables)
            if 'alembic_version' not in tables or not (legacy or current):
                raise RuntimeError('隔离测试库表结构必须是 0010 或 0011 版本')
            for table in tables:
                if table != 'alembic_version':
                    cursor.execute(f"GRANT INSERT ON `{DATABASE}`.`{table}` TO '{APP_USER}'@'127.0.0.1'")
                if table not in ('alembic_version', 'inventory_transactions', 'operation_logs', 'material_transfers', 'material_counts', 'material_adjustments'):
                    cursor.execute(f"GRANT UPDATE, DELETE ON `{DATABASE}`.`{table}` TO '{APP_USER}'@'127.0.0.1'")
    print('隔离应用账号授权完成')


if __name__ == '__main__':
    run_root, data, port = target()
    {'create': create, 'preflight': preflight, 'grants': grants}[sys.argv[1]](run_root, data, port)
