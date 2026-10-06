"""Offline 0014 -> 0015 update of a stopped, verified standalone installation."""
import argparse
import hashlib
import json
import msvcrt
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import socket
import subprocess
import sys
import time
import zipfile

# Install the startup guard first; any interrupted later swap keeps startup blocked.
UNITS = ('controller', 'backend', 'frontend', '轻仓WMS.exe', '使用说明.txt', 'package-manifest.json')
OLD_BASELINES = {'e3fbb849190498613a810f9156d02bc43d4c48e9', '90f2cf88bb9cefe0d7dfc58c0f6f9bfa2dd546e3'}
NO_WINDOW = subprocess.CREATE_NO_WINDOW


class Refusal(Exception):
    def __init__(self, code):
        self.code = code


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(root, relative):
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or any(p in ('', '.', '..') or ':' in p or '\\' in p for p in parsed.parts):
        raise Refusal(1)
    path = root
    for part in parsed.parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise Refusal(1)
    if not path.resolve().is_relative_to(root.resolve()):
        raise Refusal(1)
    return path


def verify_files(root, files):
    for name, expected in files.items():
        if digest(safe(root, name)) != expected:
            raise Refusal(1)


def settings(root, cfg):
    env = os.environ.copy()
    env.update(APP_NAME='轻仓 WMS', APP_ENV='production', MYSQL_HOST='127.0.0.1',
        MYSQL_PORT=str(cfg['mysql_port']), MYSQL_DATABASE='warehouse_standalone',
        MYSQL_USER='wms_app', MYSQL_PASSWORD=cfg['app_password'],
        MYSQL_MIGRATION_USER='wms_migrator', MYSQL_MIGRATION_PASSWORD=cfg['migration_password'],
        AUTH_SECRET=cfg['auth_secret'], AUTH_TOKEN_HOURS='8', BUSINESS_TIMEZONE='Asia/Shanghai',
        CORS_ORIGINS='[]', API_DOCS_ENABLED='false', SERVE_FRONTEND='true',
        FRONTEND_DIST=str(root/'frontend/dist'), LOG_LEVEL='INFO')
    return env


def connection(root, cfg):
    import pymysql
    con = pymysql.connect(host='127.0.0.1', port=cfg['mysql_port'], user='root',
        password=cfg['root_password'], database='warehouse_standalone', autocommit=True)
    try:
        with con.cursor() as cur:
            cur.execute('SELECT @@datadir, @@port, @@server_uuid')
            directory, port, identity = cur.fetchone()
        if Path(directory).resolve() != root/'data/mysql' or port != cfg['mysql_port'] or identity != cfg['server_uuid']:
            raise Refusal(1)
        return con
    except Exception:
        con.close()
        raise


def start_mysql(root, cfg):
    process = subprocess.Popen([str(root/'mysql/bin/mysqld.exe'), '--no-defaults',
        '--basedir='+str(root/'mysql'), '--datadir='+str(root/'data/mysql'),
        '--port='+str(cfg['mysql_port']), '--bind-address=127.0.0.1', '--mysqlx=0',
        '--local-infile=0', '--innodb-buffer-pool-size=128M', '--max-connections=50',
        '--log-error='+str(root/'data/mysql.log')], creationflags=NO_WINDOW)
    return process


def wait_mysql(root, cfg, process):
    import pymysql
    for _ in range(120):
        if process.poll() is not None:
            raise Refusal(4)
        try:
            with connection(root, cfg):
                return
        except pymysql.Error:
            time.sleep(.5)
    raise Refusal(4)


def stop_mysql(root, cfg, process):
    if process is not None and process.poll() is None:
        with connection(root, cfg) as con:
            with con.cursor() as cur:
                cur.execute('SHUTDOWN')
        process.wait(timeout=60)
        if process.returncode != 0:
            raise Refusal(4)


def snapshot(root, cfg, previous=None):
    result = {}
    with connection(root, cfg) as con:
        with con.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = {row[0] for row in cur.fetchall()} - {'alembic_version'}
            if previous is not None and tables != set(previous):
                raise Refusal(5)
            for table in sorted(tables):
                if not table.replace('_', '').isalnum():
                    raise Refusal(1)
                if previous is None:
                    cur.execute('SELECT * FROM '+chr(96)+table+chr(96))
                    columns = [entry[0] for entry in cur.description]
                else:
                    columns = previous[table]['columns']
                    cur.execute('SELECT '+','.join(chr(96)+c+chr(96) for c in columns)+' FROM '+chr(96)+table+chr(96))
                rows = sorted(json.dumps(row, ensure_ascii=True, default=str, separators=(',', ':')) for row in cur.fetchall())
                result[table] = dict(columns=columns, count=len(rows),
                    sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())
    return result


def grants(root, cfg):
    values = []
    with connection(root, cfg) as con:
        with con.cursor() as cur:
            for account in ('wms_app', 'wms_migrator'):
                cur.execute("SHOW GRANTS FOR '"+account+"'@'localhost'")
                values.extend(sorted(row[0] for row in cur.fetchall()))
    return hashlib.sha256('\n'.join(values).encode()).hexdigest()


def diagnose(root, cfg, backend, log, migrate=False):
    code = ("import sys;sys.path.insert(0,sys.argv[1]);from alembic.config import Config;"
            "from alembic import command;cfg=Config(sys.argv[1]+'/alembic.ini');")
    if migrate:
        code += "command.upgrade(cfg,'0015');"
    code += ("command.current(cfg);command.heads(cfg);command.check(cfg);"
             "from scripts.check_inventory import main;sys.exit(main())")
    result = subprocess.run([str(root/'python/python.exe'), '-I', '-c', code, str(backend)],
        cwd=backend, env=settings(root, cfg), capture_output=True, creationflags=NO_WINDOW)
    if result.returncode:
        # Do not write exception parameters, SQL rows, credentials or inherited environment.
        log.write_text('Diagnostic failed; exit='+str(result.returncode), encoding='utf-8')
        raise Refusal(5 if migrate else 3)
    log.write_bytes(result.stdout+result.stderr)


def backup(root, cfg, session):
    target = session/'before-upgrade.sql'
    options = session/('dump-'+secrets.token_hex(8)+'.cnf')
    options.write_text('[client]\nhost=127.0.0.1\nport='+str(cfg['mysql_port'])+
        '\nuser=wms_migrator\npassword='+cfg['migration_password']+'\n', encoding='utf-8')
    try:
        with target.open('xb') as output:
            status = subprocess.run([str(root/'mysql/bin/mysqldump.exe'), '--defaults-extra-file='+str(options),
                '--single-transaction', '--routines', '--triggers', '--set-gtid-purged=OFF',
                'warehouse_standalone'], stdout=output, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
            output.flush()
            os.fsync(output.fileno())
        if status.returncode:
            raise Refusal(4)
        target.with_suffix('.sql.sha256').write_text(digest(target)+'  '+target.name+'\n', encoding='ascii')
        shutil.copy2(root/'data/config.json', session/'config.json')
    finally:
        options.unlink(missing_ok=True)


def perform(root, archive):
    if root.is_symlink() or root.is_junction() or Path(sys.executable).resolve() != root/'python/python.exe':
        raise Refusal(1)
    data = safe(root, 'data')
    for required in ('data/config.json', 'data/mysql/auto.cnf', 'package-manifest.json', 'controller/runtime.py'):
        if not safe(root, required).is_file():
            raise Refusal(1)
    if (data/'upgrade-in-progress.json').exists():
        raise Refusal(6)
    cfg_file = data/'config.json'
    cfg_hash = digest(cfg_file)
    cfg = json.loads(cfg_file.read_text(encoding='utf-8'))
    if cfg.get('format') != 1 or cfg.get('ready') is not True or not cfg.get('server_uuid'):
        raise Refusal(1)
    for port in (cfg['mysql_port'], cfg['web_port']):
        if type(port) is not int or port in (80, 3306) or not 1024 <= port <= 65535:
            raise Refusal(1)
        with socket.socket() as listener:
            try:
                listener.bind(('127.0.0.1', port))
            except OSError:
                raise Refusal(2)
    with (data/'controller.lock').open('r+b') as lock:
        try:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise Refusal(2)
        old = json.loads((root/'package-manifest.json').read_text(encoding='utf-8'))
        if old.get('product') != 'LightWarehouseWMS' or old.get('format') != 1:
            raise Refusal(1)
        verify_files(root, old['files'])
        with zipfile.ZipFile(archive) as package:
            new = json.loads(package.read('upgrade-manifest.json'))
            if new['version'] != '1.3.0' or new['revision'] != '0015':
                raise Refusal(1)
            already = (old.get('version') == '1.3.0' and old.get('business_baseline') == new['business_baseline']
                       and all(old['files'].get(k) == v for k, v in new['files'].items()))
            if not already and (old.get('version') != '1.2.0' or old.get('business_baseline') not in OLD_BASELINES):
                raise Refusal(1)
            names = set(new['files']) | {'upgrade-manifest.json'}
            if len(package.infolist()) != len(names) or set(package.namelist()) != names:
                raise Refusal(1)
            for name, expected in new['files'].items():
                if PurePosixPath(name).parts[0] not in UNITS or hashlib.sha256(package.read(name)).hexdigest() != expected:
                    raise Refusal(1)
                safe(root, name)
            if digest(root/'backend/requirements-lock.txt') != new['files']['backend/requirements-lock.txt']:
                raise Refusal(1)
            # Published migrations must remain byte-identical; no hidden baseline replacement.
            for file in (root/'backend/alembic/versions').glob('*.py'):
                key = file.relative_to(root).as_posix()
                if digest(file) != new['files'].get(key):
                    raise Refusal(1)
            session = safe(root, 'data/upgrade-backups/'+time.strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(4))
            session.mkdir(parents=True)
            staged = session/'new'
            staged.mkdir()
            for name in new['files']:
                target = safe(staged, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(name))
        updated = {k:v for k,v in old['files'].items() if PurePosixPath(k).parts[0] not in UNITS}
        updated.update(new['files'])
        manifest = {**old, 'version':'1.3.0', 'business_baseline':new['business_baseline'], 'files':updated}
        (staged/'package-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        marker = data/'upgrade-in-progress.json'
        report = dict(target_revision='0015', target_baseline=new['business_baseline'], ok=False)
        process = None
        migrated = False
        swaps = []
        try:
            process = start_mysql(root, cfg)
            wait_mysql(root, cfg, process)
            with connection(root, cfg) as con:
                with con.cursor() as cur:
                    cur.execute('SELECT version_num FROM alembic_version')
                    versions = cur.fetchall()
            if versions != (('0015' if already else '0014',),):
                raise Refusal(3)
            diagnose(root, cfg, root/'backend', session/'before-check.log')
            if already:
                report.update(ok=True, already_current=True)
                return report
            before = snapshot(root, cfg)
            before_grants = grants(root, cfg)
            report.update(before=before, config_sha256=cfg_hash, grants_sha256=before_grants)
            backup(root, cfg, session)
            old_dir = session/'old'
            old_dir.mkdir()
            with marker.open('x', encoding='utf-8') as marker_file:
                json.dump(dict(backup=str(session), revision='0015'), marker_file)
                marker_file.flush()
                os.fsync(marker_file.fileno())
            for unit in UNITS:
                original = safe(root, unit)
                original.rename(old_dir/unit)
                swaps.append(unit)
                (staged/unit).rename(original)
            migrated = True
            diagnose(root, cfg, root/'backend', session/'after-check.log', migrate=True)
            after = snapshot(root, cfg, before)
            if before != after or digest(cfg_file) != cfg_hash or grants(root, cfg) != before_grants:
                raise Refusal(5)
            with connection(root, cfg) as con:
                with con.cursor() as cur:
                    cur.execute('SELECT version_num FROM alembic_version')
                    if cur.fetchall() != (('0015',),):
                        raise Refusal(5)
                    cur.execute('SELECT COUNT(*) FROM stock_documents WHERE material_outbound_purpose IS NOT NULL')
                    if cur.fetchone()[0] != 0:
                        raise Refusal(5)
            verify_files(root, manifest['files'])
            report.update(ok=True, after=after, historical_rows_unchanged=True,
                          config_unchanged=True, grants_unchanged=True, backup=str(session))
            stop_mysql(root, cfg, process)
            process = None
            marker.unlink()
            return report
        except Exception as exc:
            report.update(ok=False, error_code=exc.code if isinstance(exc, Refusal) else 5,
                          error_type=type(exc).__name__, migration_started=migrated)
            if swaps and not migrated:
                failed = session/'failed'
                failed.mkdir(exist_ok=True)
                for unit in reversed(swaps):
                    target = safe(root, unit)
                    if target.exists():
                        target.rename(failed/unit)
                    (session/'old'/unit).rename(target)
                marker.unlink(missing_ok=True)
            raise
        finally:
            try:
                stop_mysql(root, cfg, process)
            finally:
                (session/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--payload', type=Path, required=True)
    args = parser.parse_args()
    try:
        perform(args.root.absolute(), args.payload.resolve())
        return 0
    except Refusal as exc:
        return exc.code
    except Exception:
        return 5


if __name__ == '__main__':
    sys.exit(main())
