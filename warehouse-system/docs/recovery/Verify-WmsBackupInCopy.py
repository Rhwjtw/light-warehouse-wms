"""Restore the verified old SQL into a NEW private instance; never import into copy/source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import traceback

import pymysql

NO_WINDOW = subprocess.CREATE_NO_WINDOW
UNITS = {'controller', 'backend', 'frontend', '轻仓WMS.exe', '使用说明.txt', 'package-manifest.json'}
BASELINES = {'e3fbb849190498613a810f9156d02bc43d4c48e9', '90f2cf88bb9cefe0d7dfc58c0f6f9bfa2dd546e3'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def store(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def safe(root, relative):
    parts = Path(relative)
    if parts.is_absolute() or '..' in parts.parts or ':' in str(parts):
        raise RuntimeError('UNSAFE_PATH')
    result = (root / parts).resolve()
    if not result.is_relative_to(root):
        raise RuntimeError('OUTSIDE_VALIDATION_ROOT')
    return result


def free(port):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))


def connect(data, port, password, identity=None, database=None):
    con = pymysql.connect(host='127.0.0.1', port=port, user='root', password=password,
                          database=database, autocommit=True, connect_timeout=2, charset='utf8mb4')
    try:
        with con.cursor() as cur:
            cur.execute('SELECT @@datadir,@@port,@@server_uuid')
            directory, actual_port, uuid = cur.fetchone()
        if Path(directory).resolve() != data or actual_port != port or (identity and identity != uuid):
            raise RuntimeError('INSTANCE_IDENTITY_MISMATCH')
        return con, uuid
    except Exception:
        con.close()
        raise


def start(mysql, data, port, log):
    free(port)
    return subprocess.Popen([str(mysql / 'bin/mysqld.exe'), '--no-defaults',
        '--basedir=' + str(mysql), '--datadir=' + str(data), '--port=' + str(port),
        '--bind-address=127.0.0.1', '--mysqlx=0', '--local-infile=0',
        '--innodb-buffer-pool-size=128M', '--max-connections=20', '--log-error=' + str(log)],
        creationflags=NO_WINDOW)


def wait(process, data, port, password, identity=None, database=None):
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError('VALIDATION_MYSQL_START_FAILED')
        try:
            return connect(data, port, password, identity, database)
        except pymysql.Error:
            time.sleep(.5)
    raise RuntimeError('VALIDATION_MYSQL_START_TIMEOUT')


def tables(con):
    with con.cursor() as cur:
        cur.execute('SHOW TABLES')
        names = {row[0] for row in cur.fetchall()} - {'alembic_version'}
    if not names or not all(re.fullmatch(r'[A-Za-z0-9_]+',name) for name in names):
        raise RuntimeError('INVALID_TABLE_SET')
    return names


def snapshot(con, columns=None):
    result = {}
    for name in sorted(tables(con)):
        selected = columns[name]['columns'] if columns else None
        if selected and not all(re.fullmatch(r'[A-Za-z0-9_]+', col) for col in selected):
            raise RuntimeError('INVALID_COLUMN_NAME')
        with con.cursor() as cur:
            cur.execute('SELECT ' + (','.join('`'+col+'`' for col in selected) if selected else '*') + ' FROM `'+name+'`')
            selected = selected or [entry[0] for entry in cur.description]
            rows = sorted(json.dumps(row, ensure_ascii=True, default=str, separators=(',', ':')) for row in cur.fetchall())
        result[name] = {'columns':selected, 'count':len(rows), 'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--copy', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--restore-port', type=int, required=True)
    args = parser.parse_args()
    root = args.copy.resolve()
    out = root.parent
    if root.name != 'working' or Path(sys.executable).resolve() != root/'python/python.exe':
        raise RuntimeError('USE_VERIFIED_WORKING_COPY_INTERPRETER')
    inspection = read(out/'inspection.json')
    proof = read(out/'copy-verification.json')
    if inspection.get('ok') is not True or inspection.get('copy_only') is not True or proof.get('sha256_all_equal') is not True or proof.get('test_port') != args.port:
        raise RuntimeError('CURRENT_COPY_INSPECTION_REQUIRED')
    validation = out/'backup-validation'
    if validation.exists() or (out/'backup-validation.json').exists():
        raise RuntimeError('USE_A_NEW_DIAGNOSTIC_RUN')
    for entry in root.rglob('*'):
        if entry.is_symlink() or entry.is_junction():
            raise RuntimeError('LINK_NOT_ALLOWED')
    cfg = read(root/'data/config.json')
    if digest(root/'data/config.json') != inspection['original_config_sha256']:
        raise RuntimeError('COPY_CONFIG_CHANGED')
    if args.port in (80,3306,cfg['mysql_port'],cfg['web_port']) or args.restore_port in (80,3306,args.port,cfg['mysql_port'],cfg['web_port']) or not all(1024 <= p <= 65535 for p in (args.port,args.restore_port)):
        raise RuntimeError('INVALID_VALIDATION_PORTS')
    name = inspection.get('backup_session','')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',name):
        raise RuntimeError('ORIGINAL_BACKUP_SESSION_REQUIRED')
    session = safe(root,'data/upgrade-backups/'+name)
    sql = session/'before-upgrade.sql'
    if digest(sql) != (session/'before-upgrade.sql.sha256').read_text(encoding='ascii').split()[0]:
        raise RuntimeError('BACKUP_SQL_SHA_MISMATCH')
    if digest(session/'config.json') != inspection['original_config_sha256']:
        raise RuntimeError('BACKUP_CONFIG_MISMATCH')
    old = session/'old'
    manifest = read(old/'package-manifest.json')
    if manifest.get('product') != 'LightWarehouseWMS' or manifest.get('format') != 1 or manifest.get('version') != '1.2.0' or manifest.get('business_baseline') not in BASELINES:
        raise RuntimeError('VERIFIED_0014_ORIGINAL_PACKAGE_REQUIRED')
    for relative, expected in manifest['files'].items():
        file = safe(old if Path(relative).parts[0] in UNITS else root,relative)
        if not file.is_file() or digest(file) != expected:
            raise RuntimeError('ORIGINAL_PACKAGE_FILE_MISMATCH')
    marker = root/'data/upgrade-in-progress.json'
    marker_sha = digest(marker)
    free(args.port)
    free(args.restore_port)
    validation.mkdir()
    data = validation/'data/mysql'
    data.parent.mkdir()
    mysql = root/'mysql'
    result = {'ok':False,'copy_only':True,'recovery_authorized':False,'phase':'initialize_new_instance',
              'restore_port':args.restore_port,'sql_sha256':digest(sql),'old_package_sha_pass':True}
    current_process = restore_process = current_con = restore_con = None
    restore_cfg = {'format':1,'root_password':secrets.token_urlsafe(36),
                   'app_password':secrets.token_urlsafe(36),'migration_password':secrets.token_urlsafe(36),
                   'auth_secret':secrets.token_urlsafe(48),'port':args.restore_port}
    store(validation/'private-config.json',restore_cfg)
    try:
        initialize = subprocess.run([str(mysql/'bin/mysqld.exe'),'--no-defaults','--initialize-insecure',
            '--basedir='+str(mysql),'--datadir='+str(data),'--log-error='+str(validation/'initialize.log')],
            capture_output=True, creationflags=NO_WINDOW, timeout=180)
        if initialize.returncode:
            raise RuntimeError('EMPTY_VALIDATION_INSTANCE_INITIALIZATION_FAILED')
        result['phase'] = 'restore_sql_into_empty_instance'
        restore_process = start(mysql,data,args.restore_port,validation/'mysql.log')
        restore_con, uuid = wait(restore_process,data,args.restore_port,'')
        if uuid == cfg['server_uuid']:
            raise RuntimeError('RESTORE_UUID_MUST_BE_NEW')
        restore_cfg['server_uuid'] = uuid
        store(validation/'private-config.json',restore_cfg)
        with restore_con.cursor() as cur:
            cur.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s",(restore_cfg['root_password'],))
            cur.execute('CREATE DATABASE warehouse_standalone CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        options = validation/'import.cnf'
        options.write_text('[client]\nhost=127.0.0.1\nport='+str(args.restore_port)+'\nuser=root\npassword='+restore_cfg['root_password']+'\n',encoding='utf8')
        try:
            with sql.open('rb') as stream:
                imported = subprocess.run([str(mysql/'bin/mysql.exe'),'--defaults-extra-file='+str(options),
                    '--protocol=TCP','--binary-mode','--local-infile=0','warehouse_standalone'],
                    stdin=stream,capture_output=True,creationflags=NO_WINDOW,timeout=300)
            if imported.returncode:
                raise RuntimeError('SQL_RESTORE_IN_EMPTY_INSTANCE_FAILED')
        finally:
            options.unlink(missing_ok=True)
        with restore_con.cursor() as cur:
            cur.execute('USE warehouse_standalone')
            cur.execute('SELECT version_num FROM alembic_version')
            result['restored_revision'] = [row[0] for row in cur.fetchall()]
            if result['restored_revision'] != ['0014']:
                raise RuntimeError('BACKUP_IS_NOT_0014')
            cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='warehouse_standalone' AND table_name='stock_documents' AND column_name='material_outbound_purpose'")
            if cur.fetchone()[0]:
                raise RuntimeError('BACKUP_STRUCTURE_IS_NOT_0014')
            for table in sorted(tables(restore_con) | {'alembic_version'}):
                cur.execute('CHECK TABLE `'+table+'`')
                if not all(row[2]=='status' and row[3]=='OK' for row in cur.fetchall()):
                    raise RuntimeError('RESTORED_TABLE_CHECK_FAILED')
            for account, password in [('wms_app',restore_cfg['app_password']),('wms_migrator',restore_cfg['migration_password'])]:
                cur.execute("CREATE USER %s@'localhost' IDENTIFIED BY %s",(account,password))
                cur.execute("GRANT SELECT ON warehouse_standalone.* TO '"+account+"'@'localhost'")
        result['phase'] = 'validate_restored_0014'
        backend = old/'backend'
        env = os.environ.copy()
        env.update(APP_ENV='production',MYSQL_HOST='127.0.0.1',MYSQL_PORT=str(args.restore_port),
            MYSQL_DATABASE='warehouse_standalone',MYSQL_USER='wms_app',MYSQL_PASSWORD=restore_cfg['app_password'],
            MYSQL_MIGRATION_USER='wms_migrator',MYSQL_MIGRATION_PASSWORD=restore_cfg['migration_password'],
            AUTH_SECRET=restore_cfg['auth_secret'],BUSINESS_TIMEZONE='Asia/Shanghai',CORS_ORIGINS='[]',
            API_DOCS_ENABLED='false',SERVE_FRONTEND='false')
        code="import sys;sys.path.insert(0,sys.argv[1]);from alembic.config import Config;from alembic import command;c=Config(sys.argv[1]+'/alembic.ini');command.current(c);command.heads(c);command.check(c);from scripts.check_inventory import main;sys.exit(main())"
        checked = subprocess.run([str(root/'python/python.exe'),'-I','-c',code,str(backend)],
            cwd=backend,env=env,capture_output=True,creationflags=NO_WINDOW,timeout=180)
        result['restored_alembic_inventory_exit'] = checked.returncode
        if checked.returncode:
            raise RuntimeError('RESTORED_MODEL_OR_INVENTORY_CHECK_FAILED')
        (validation/'restored-check.log').write_bytes(checked.stdout+checked.stderr)
        restored = snapshot(restore_con)
        result['phase'] = 'compare_current_copy_old_columns'
        current_process = start(mysql,root/'data/mysql',args.port,validation/'current-copy-mysql.log')
        current_con, _ = wait(current_process,root/'data/mysql',args.port,cfg['root_password'],cfg['server_uuid'],'warehouse_standalone')
        with current_con.cursor() as cur:
            cur.execute('SELECT version_num FROM alembic_version')
            result['current_revision'] = [row[0] for row in cur.fetchall()]
        if result['current_revision'] != inspection['database_revision'] or snapshot(current_con,inspection['row_fingerprints']) != inspection['row_fingerprints']:
            raise RuntimeError('CURRENT_COPY_CHANGED_SINCE_INSPECTION')
        if tables(current_con) != set(restored):
            raise RuntimeError('BUSINESS_TABLE_SET_DIFFERS')
        current = snapshot(current_con,restored)
        result['old_rows_equal'] = current == restored
        result['different_tables'] = [name for name in restored if restored[name] != current[name]]
        result['table_count'] = len(restored)
        result['restored_old_fingerprints'] = restored
        if not result['old_rows_equal']:
            raise RuntimeError('CURRENT_DATA_DIFFERS_FROM_UPGRADE_BACKUP_STOP')
        if digest(sql) != result['sql_sha256']:
            raise RuntimeError('BACKUP_SQL_CHANGED_DURING_VALIDATION_STOP')
        result.update(ok=True,phase='complete',marker_preserved=True,config_preserved=True)
    except Exception as exc:
        result.update(ok=False,error_type=type(exc).__name__)
        if isinstance(exc,RuntimeError) and re.fullmatch(r'[A-Z0-9_]+',str(exc)):
            result['stop_reason'] = str(exc)
        raise
    finally:
        for con, process in [(current_con,current_process),(restore_con,restore_process)]:
            try:
                if con is not None:
                    with con.cursor() as cur:
                        cur.execute('SHUTDOWN')
                    process.wait(timeout=60)
                    if process.returncode != 0:
                        raise RuntimeError('VALIDATION_MYSQL_STOP_FAILED')
                elif process is not None and process.poll() is None:
                    raise RuntimeError('OWNED_INSTANCE_SHUTDOWN_UNVERIFIED_STOP')
            except Exception as exc:
                result.update(ok=False,shutdown_error_type=type(exc).__name__)
            finally:
                if con is not None:
                    con.close()
        if digest(marker) != marker_sha or digest(root/'data/config.json') != inspection['original_config_sha256']:
            result.update(ok=False,stop_reason='MARKER_OR_CONFIG_CHANGED_STOP')
        store(out/'backup-validation.json',result)
    print(json.dumps({key:result.get(key) for key in ('ok','current_revision','restored_revision','old_rows_equal','table_count','recovery_authorized')},ensure_ascii=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        error={'ok':False,'exception_type':type(exc).__name__,
            'frames':[(Path(frame.filename).name,frame.lineno) for frame in traceback.extract_tb(exc.__traceback__)[-4:]]}
        if isinstance(exc,RuntimeError) and re.fullmatch(r'[A-Z0-9_]+',str(exc)):
            error['stop_reason']=str(exc)
        print(json.dumps(error))
        sys.exit(1)
