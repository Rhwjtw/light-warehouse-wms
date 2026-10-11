"""Manage only the private MySQL instance installed beside this controller.

No source .env, production credentials, service names or network addresses are used.
"""
import hashlib
import json
import logging
import msvcrt
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import traceback
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
NO_WINDOW = subprocess.CREATE_NO_WINDOW
OWNED_MYSQL = None
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')


def event(kind, **values):
    print(json.dumps({'event': kind, **values}, ensure_ascii=False), flush=True)


def save_config(path, values):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, indent=2), encoding='utf-8')
    temporary.replace(path)


def available_port(first):
    for port in range(first, first + 100):
        with socket.socket() as listener:
            try:
                listener.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError('NO_FREE_PORT')


def restrict_directory(data):
    # App installation/data remain owned by the installing Windows user.
    import ctypes
    from ctypes import wintypes
    token = wintypes.HANDLE()
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise RuntimeError('TOKEN_ACCESS_FAILED')
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise RuntimeError('TOKEN_READ_FAILED')
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_text = wintypes.LPWSTR()
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise RuntimeError('SID_READ_FAILED')
        try:
            identity = sid_text.value
        finally:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)
    subprocess.run(['icacls.exe', str(data), '/inheritance:r', '/grant:r',
                    '*' + identity + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F',
                    '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True,
                   creationflags=NO_WINDOW)


def environment(config):
    # Explicitly replace all app configuration; never inherit another WMS .env.
    values = {
        'APP_NAME': '轻仓 WMS', 'APP_ENV': 'production', 'MYSQL_HOST': '127.0.0.1',
        'MYSQL_PORT': str(config['mysql_port']), 'MYSQL_DATABASE': 'warehouse_standalone',
        'MYSQL_USER': 'wms_app', 'MYSQL_PASSWORD': config['app_password'],
        'MYSQL_MIGRATION_USER': 'wms_migrator', 'MYSQL_MIGRATION_PASSWORD': config['migration_password'],
        'AUTH_SECRET': config['auth_secret'], 'AUTH_TOKEN_HOURS': '8',
        'STANDALONE_PASSWORD_POLICY': 'true',
        'BUSINESS_TIMEZONE': 'Asia/Shanghai', 'CORS_ORIGINS': '[]',
        'API_DOCS_ENABLED': 'false', 'SERVE_FRONTEND': 'true',
        'FRONTEND_DIST': str(ROOT / 'frontend' / 'dist'), 'LOG_LEVEL': 'INFO',
    }
    os.environ.update(values)


def connect(config, password=None):
    import pymysql
    return pymysql.connect(host='127.0.0.1', port=config['mysql_port'], user='root',
                           password=config['root_password'] if password is None else password,
                           charset='utf8mb4', autocommit=True, connect_timeout=2)


def verify_instance(connection, config, data):
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@datadir, @@port, @@server_uuid')
        directory, port, identity = cursor.fetchone()
    if Path(directory).resolve() != (data / 'mysql').resolve() or port != config['mysql_port']:
        raise RuntimeError('INSTANCE_PATH_MISMATCH')
    if config.get('server_uuid') and config['server_uuid'] != identity:
        raise RuntimeError('INSTANCE_UUID_MISMATCH')
    return identity


def start_mysql(config, data):
    global OWNED_MYSQL
    mysql_base = ROOT / 'mysql'
    executable = mysql_base / 'bin' / 'mysqld.exe'
    if not (data / 'mysql').exists():
        event('progress', message='正在创建独立数据库，首次启动可能需要几分钟…')
        subprocess.run([str(executable), '--no-defaults', '--initialize-insecure',
                        '--basedir=' + str(mysql_base), '--datadir=' + str(data / 'mysql'),
                        '--log-error=' + str(data / 'mysql-initialize.log')],
                       check=True, creationflags=NO_WINDOW)
    arguments = [str(executable), '--no-defaults', '--basedir=' + str(mysql_base),
                 '--datadir=' + str(data / 'mysql'), '--port=' + str(config['mysql_port']),
                 '--bind-address=127.0.0.1', '--mysqlx=0', '--local-infile=0',
                 '--innodb-buffer-pool-size=128M', '--max-connections=50',
                 '--log-error=' + str(data / 'mysql.log')]
    # Refuse occupied ports rather than connect to or stop an existing database.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', config['mysql_port']))
    process = subprocess.Popen(arguments, creationflags=NO_WINDOW)
    OWNED_MYSQL = process
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError('MYSQL_START_FAILED')
        for password in ([config['root_password'], ''] if not config['ready'] else [config['root_password']]):
            try:
                with connect(config, password) as connection:
                    identity = verify_instance(connection, config, data)
                    if password == '':
                        with connection.cursor() as cursor:
                            cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (config['root_password'],))
                    config['server_uuid'] = identity
                    save_config(data / 'config.json', config)
                    return process
            except Exception as exc:
                if isinstance(exc, RuntimeError):
                    raise
        time.sleep(0.5)
    raise RuntimeError('MYSQL_START_TIMEOUT')


def initialize(config, data, credentials):
    from alembic import command
    from alembic.config import Config
    with connect(config) as connection:
        verify_instance(connection, config, data)
        with connection.cursor() as cursor:
            cursor.execute('CREATE DATABASE IF NOT EXISTS warehouse_standalone CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            for user, password in (('wms_app', config['app_password']), ('wms_migrator', config['migration_password'])):
                cursor.execute("CREATE USER IF NOT EXISTS %s@'localhost' IDENTIFIED BY %s", (user, password))
            cursor.execute("GRANT SELECT ON warehouse_standalone.* TO 'wms_app'@'localhost'")
            cursor.execute("GRANT ALL PRIVILEGES ON warehouse_standalone.* TO 'wms_migrator'@'localhost'")
    event('progress', message='正在配置系统和管理员账号…')
    command.upgrade(Config(str(BACKEND / 'alembic.ini')), 'head')
    # Same immutable-fact and column-update rules as scripts/bootstrap_mysql.py.
    append_only = {'stock_documents', 'inventory_transactions', 'operation_logs',
                   'material_transfers', 'material_counts', 'material_adjustments',
                   'material_issue_records', 'finished_receipts', 'finished_outbounds'}
    column_updates = {'material_movements': ('shelved_quantity',),
                      'material_batches': ('received_quantity', 'remaining_quantity', 'updated_at'),
                      'finished_batches': ('remaining_quantity', 'updated_at')}
    with connect(config) as connection:
        verify_instance(connection, config, data)
        with connection.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA='warehouse_standalone'")
            for (table,) in cursor.fetchall():
                if table == 'alembic_version':
                    continue
                cursor.execute(f"GRANT INSERT ON warehouse_standalone.`{table}` TO 'wms_app'@'localhost'")
                if table in column_updates:
                    columns = ','.join('`' + value + '`' for value in column_updates[table])
                    cursor.execute(f"GRANT UPDATE ({columns}) ON warehouse_standalone.`{table}` TO 'wms_app'@'localhost'")
                elif table not in append_only:
                    cursor.execute(f"GRANT UPDATE,DELETE ON warehouse_standalone.`{table}` TO 'wms_app'@'localhost'")
    from scripts.init_admin import ensure_initial_admin
    from app.database import SessionLocal
    with SessionLocal.begin() as session:
        ensure_initial_admin(session, username=credentials['username'], display_name='系统管理员',
                             password=credentials['password'], standalone=True)
    config['ready'] = True
    save_config(data / 'config.json', config)


def backup(config, data):
    with connect(config) as connection:
        verify_instance(connection, config, data)
    directory = data / 'backups'
    directory.mkdir(exist_ok=True)
    target = directory / ('warehouse_' + time.strftime('%Y%m%d_%H%M%S') + '_' + secrets.token_hex(3) + '.sql')
    options = data / ('dump-' + secrets.token_hex(8) + '.cnf')
    options.write_text('[client]\nhost=127.0.0.1\nport=' + str(config['mysql_port']) +
                       '\nuser=wms_migrator\npassword=' + config['migration_password'] + '\n', encoding='utf-8')
    try:
        with target.open('xb') as output:
            result = subprocess.run([str(ROOT / 'mysql' / 'bin' / 'mysqldump.exe'),
                                     '--defaults-extra-file=' + str(options), '--single-transaction',
                                     '--routines', '--triggers', '--set-gtid-purged=OFF', 'warehouse_standalone'],
                                    stdout=output, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        if result.returncode:
            target.unlink(missing_ok=True)
            raise RuntimeError('BACKUP_FAILED')
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        target.with_suffix('.sql.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
        return str(target)
    finally:
        options.unlink(missing_ok=True)


def run(data):
    if (data / 'upgrade-in-progress.json').exists():
        raise RuntimeError('UPGRADE_INCOMPLETE_DATA_PRESERVED')
    import uvicorn  # Verify installed dependency before initializing any database.
    data.mkdir(parents=True, exist_ok=True)
    if data.is_symlink() or data.is_junction():
        raise RuntimeError('DATA_LINK_NOT_ALLOWED')
    restrict_directory(data)
    with (data / 'controller.lock').open('a+b') as lock:
        lock.seek(0)
        if lock.read(1) == b'':
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        config_path = data / 'config.json'
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding='utf-8'))
            if config.get('format') != 1:
                raise RuntimeError('CONFIG_FORMAT_MISMATCH')
        else:
            if (data / 'mysql').exists():
                raise RuntimeError('EXISTING_DATA_WITHOUT_CONFIG')
            config = dict(format=1, ready=False, mysql_port=available_port(33406),
                          web_port=available_port(18086), root_password=secrets.token_urlsafe(36),
                          app_password=secrets.token_urlsafe(36), migration_password=secrets.token_urlsafe(36),
                          auth_secret=secrets.token_urlsafe(48))
            save_config(config_path, config)
        credentials = json.loads(sys.stdin.readline())
        if not config['ready']:
            # Reject invalid credentials before spending time initializing MySQL.
            import re
            username, password = credentials.get('username', ''), credentials.get('password', '')
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,63}', username) or
                    not isinstance(password, str) or not 4 <= len(password) <= 10):
                raise RuntimeError('INVALID_INITIAL_ADMIN')
        environment(config)
        mysql = server = None
        try:
            mysql = start_mysql(config, data)
            if not config['ready']:
                initialize(config, data, credentials)
            with connect(config) as version_connection:
                verify_instance(version_connection, config, data)
                with version_connection.cursor() as version_cursor:
                    version_cursor.execute('SELECT version_num FROM warehouse_standalone.alembic_version')
                    if version_cursor.fetchall() != (('0015',),):
                        raise RuntimeError('DATABASE_UPGRADE_REQUIRED')
            credentials.clear()
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', config['web_port']))
            url = 'http://127.0.0.1:' + str(config['web_port'])
            with (data / 'backend-console.log').open('ab') as server_log:
                server = subprocess.Popen([sys.executable, '-I', str(Path(__file__)), 'serve', str(data)],
                                          stdout=server_log, stderr=server_log, creationflags=NO_WINDOW)
                request = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(120):
                    if server.poll() is not None:
                        raise RuntimeError('BACKEND_START_FAILED')
                    try:
                        with request.open(url + '/api/health/db', timeout=2) as response:
                            result = json.load(response)
                        if result['data']['migration'] == '0015' and result['data']['status'] == 'ok':
                            break
                    except Exception:
                        time.sleep(0.5)
                else:
                    raise RuntimeError('BACKEND_START_TIMEOUT')
                event('ready', url=url)
                for line in sys.stdin:
                    action = line.strip()
                    if action == 'stop':
                        break
                    if action == 'backup':
                        try:
                            event('backup', path=backup(config, data))
                        except Exception as exc:
                            event('error', message='备份失败，请检查数据目录和日志。', error=type(exc).__name__)
        finally:
            if server is not None and server.poll() is None:
                server.terminate()
                server.wait(timeout=30)
            mysql = mysql or OWNED_MYSQL
            if mysql is not None and mysql.poll() is None:
                shutdown = False
                for password in ([config['root_password'], ''] if not config['ready'] else [config['root_password']]):
                    try:
                        with connect(config, password) as connection:
                            verify_instance(connection, config, data)
                            with connection.cursor() as cursor:
                                cursor.execute('SHUTDOWN')
                        shutdown = True
                        break
                    except Exception:
                        continue
                if not shutdown:
                    raise RuntimeError('MYSQL_SHUTDOWN_FAILED')
                mysql.wait(timeout=30)
            event('stopped')


def main():
    action, data = sys.argv[1], Path(sys.argv[2])
    if (ROOT / 'data').is_symlink() or (ROOT / 'data').is_junction():
        raise RuntimeError('DATA_LINK_NOT_ALLOWED')
    data = data.resolve()
    if data != (ROOT / 'data').resolve():
        raise RuntimeError('DATA_PATH_OUTSIDE_INSTALLATION')
    if action == 'serve':
        environment(json.loads((data / 'config.json').read_text(encoding='utf-8')))
        from logging.handlers import RotatingFileHandler
        logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
            data / 'backend.log', maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')],
            format='%(asctime)s %(levelname)s %(name)s %(message)s')
        import uvicorn
        uvicorn.run('app.main:app', host='127.0.0.1', port=int(json.loads((data / 'config.json').read_text())['web_port']),
                    access_log=False, log_config=None)
    elif action == 'run':
        run(data)
    else:
        raise RuntimeError('UNKNOWN_ACTION')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        message = '系统启动或停止失败，请查看安装目录data中的日志；原数据保留。'
        if isinstance(exc, RuntimeError) and str(exc) == 'UPGRADE_INCOMPLETE_DATA_PRESERVED':
            message = '检测到上次安装升级未完成。请重新运行最新完整安装包，程序会核验并尝试继续；原数据保留。'
        elif isinstance(exc, RuntimeError) and str(exc) == 'INVALID_INITIAL_ADMIN':
            message = '请检查管理员用户名和密码：密码需为4～10个字符，可使用纯数字。'
        elif isinstance(exc, RuntimeError) and str(exc) == 'DATABASE_UPGRADE_REQUIRED':
            message = '程序与数据库版本尚未匹配，请运行最新完整安装包完成升级；原数据保留。'
        event('error', message=message, error=type(exc).__name__,
              location=' <- '.join(f'{Path(f.filename).name}:{f.lineno}' for f in frames[-4:]))
        sys.exit(1)
