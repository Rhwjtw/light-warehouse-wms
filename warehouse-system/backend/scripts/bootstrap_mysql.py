"""显式运行的本地初始化工具；只操作指定新库及两个专用账号，不修改 root。"""
import re
import secrets
import sys
import subprocess
from pathlib import Path

import pymysql
from dotenv import dotenv_values, set_key

ENV = Path(__file__).resolve().parents[1] / '.env'


def main():
    values = dotenv_values(ENV)
    database = values.get('MYSQL_DATABASE', 'warehouse_system')
    app_user = values.get('MYSQL_USER', 'warehouse_app')
    migration_user = values.get('MYSQL_MIGRATION_USER') or 'warehouse_migrator'
    for identifier in (database, app_user, migration_user):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,47}', identifier):
            raise ValueError('数据库和账号名只能包含英文字母、数字、下划线，最长 48 字符')
    if app_user == migration_user or 'root' in (app_user, migration_user):
        raise ValueError('必须使用两个不同的专用账号，不可使用 root')
    if not values.get('MYSQL_ADMIN_PASSWORD'):
        raise ValueError('请先在 .env 填写 MYSQL_ADMIN_PASSWORD')
    # 先持久化随机凭据；重复运行时复用，不重置已存在用户的密码。
    for key in ('MYSQL_PASSWORD', 'MYSQL_MIGRATION_PASSWORD', 'AUTH_SECRET'):
        if not values.get(key):
            values[key] = secrets.token_urlsafe(36)
            set_key(str(ENV), key, values[key])
    set_key(str(ENV), 'MYSQL_MIGRATION_USER', migration_user)
    with pymysql.connect(host=values.get('MYSQL_HOST', '127.0.0.1'),
                         port=int(values.get('MYSQL_PORT', '3306')),
                         user=values.get('MYSQL_ADMIN_USER', 'root'),
                         password=values['MYSQL_ADMIN_PASSWORD'], charset='utf8mb4',
                         connect_timeout=5, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            for user, key, grants in ((app_user, 'MYSQL_PASSWORD', 'SELECT, INSERT'),
                                       (migration_user, 'MYSQL_MIGRATION_PASSWORD', 'ALL PRIVILEGES')):
                cursor.execute('CREATE USER IF NOT EXISTS %s@\'localhost\' IDENTIFIED BY %s', (user, values[key]))
                if user == app_user:
                    # 收紧本项目库级权限；不修改其他库或服务器设置。
                    cursor.execute('SHOW GRANTS FOR %s@\'localhost\'', (user,))
                    if any(f'`{database}`.*' in row[0] for row in cursor.fetchall()):
                        cursor.execute(f'REVOKE ALL PRIVILEGES ON `{database}`.* FROM %s@\'localhost\'', (user,))
                cursor.execute(f'GRANT {grants} ON `{database}`.* TO %s@\'localhost\'', (user,))
            subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ENV.parent, check=True)
            cursor.execute('SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA = %s', (database,))
            for (table,) in cursor.fetchall():
                if table not in ('inventory_transactions', 'operation_logs', 'alembic_version'):
                    cursor.execute(f'GRANT UPDATE, DELETE ON `{database}`.`{table}` TO %s@\'localhost\'', (app_user,))
            cursor.execute('SELECT VERSION()')
            print('MySQL', cursor.fetchone()[0], ': database and local application/migration accounts ready')
    # 应用服务不需要管理员密码；初始化成功后立即移除。
    set_key(str(ENV), 'MYSQL_ADMIN_PASSWORD', '')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'初始化失败 ({type(exc).__name__})。请检查本地 .env 的管理员凭据、服务和端口；不会显示密码。', file=sys.stderr)
        sys.exit(1)
