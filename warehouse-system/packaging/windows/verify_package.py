"""Real installed-runtime acceptance restricted to a new .runtime installation."""
import argparse
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

import pymysql

APP = Path(__file__).resolve().parents[2]


class Controller:
    def __init__(self, root, credentials):
        self.events = queue.Queue()
        self.process = subprocess.Popen([str(root / 'python/python.exe'), '-I', str(root / 'controller/runtime.py'),
            'run', str(root / 'data')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        def read():
            for line in self.process.stdout:
                self.events.put(json.loads(line))
            self.events.put({'event': 'exit'})
        threading.Thread(target=read, daemon=True).start()
        self.process.stdin.write(json.dumps(credentials) + '\n')
        self.process.stdin.flush()
        self.url = self.wait('ready')['url']

    def wait(self, desired):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            item = self.events.get(timeout=180)
            if item['event'] in {'error', 'exit'}:
                raise RuntimeError(json.dumps(item, ensure_ascii=False))
            if item['event'] == desired:
                return item
        raise TimeoutError(desired)

    def backup(self):
        self.process.stdin.write('backup\n'); self.process.stdin.flush()
        return Path(self.wait('backup')['path'])

    def stop(self):
        if self.process.poll() is None:
            self.process.stdin.write('stop\n'); self.process.stdin.flush()
            self.process.wait(timeout=90)
        assert self.process.returncode == 0, 'controller did not stop cleanly'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report = args.report.resolve()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    root = args.install_root.resolve()
    if not root.is_relative_to(APP / '.runtime') or (root / 'data').exists():
        raise RuntimeError('Acceptance requires a new isolated installation under .runtime')
    manifest = json.loads((root / 'package-manifest.json').read_text(encoding='utf-8'))
    for relative, digest in manifest['files'].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, relative
    expected_revision = '0015' if manifest['version'] in ('1.3.0','1.3.1') else '0014'
    report = {'checks': ['package file hashes verified'], 'business_baseline': manifest['business_baseline']}
    credentials = {'username': 'installadmin', 'password': '安装验收密码' + secrets.token_urlsafe(24)}
    controller = None
    token = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(method, path, body=None, expected=200):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(controller.url + path, data=data, headers=headers, method=method)
        try:
            response = opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            actual = response.status
            result = json.load(response)
        assert actual == expected, f'{method} {path}: status {actual} != {expected}'
        return result['data']

    try:
        controller = Controller(root, credentials)
        cfg = json.loads((root / 'data/config.json').read_text(encoding='utf-8'))
        assert cfg['ready'] and cfg['mysql_port'] != 3306 and cfg['web_port'] != 80
        def db(user='root', database='warehouse_standalone'):
            password = cfg['root_password'] if user == 'root' else cfg['app_password']
            connection = pymysql.connect(host='127.0.0.1', port=cfg['mysql_port'], user=user, password=password,
                                         database=database, autocommit=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT @@datadir, @@server_uuid')
                directory, identity = cursor.fetchone()
            assert Path(directory).resolve() == root / 'data/mysql' and identity == cfg['server_uuid']
            return connection
        with db() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM inventory_transactions')
                assert cursor.fetchone()[0] == 0
                cursor.execute('SELECT COUNT(*) FROM roles')
                assert cursor.fetchone()[0] == 5
        report['checks'].append('fresh database, five roles, random separate database accounts, empty inventory')
        token = request('POST', '/api/auth/login', credentials)['access_token']
        for path in ('/api/auth/me', '/api/material-stock', '/api/finished-stock', '/api/production-orders',
                     '/api/traceability/search?q=install', '/api/roles'):
            request('GET', path)
        request('GET', '/api/docs', expected=404)
        name = '独立安装验收'
        product = request('POST', '/api/products', {'code':'INSTALL_PRODUCT', 'name':name, 'unit':'盒'})
        today = date.today().isoformat()
        def posting(path, values, expected=200):
            return request('POST', '/api/' + path, {'request_id':uuid4().hex, **values}, expected)
        receipt = posting('finished-receipts', {'product_id':product['id'], 'quantity':'5.123456', 'unit':'盒',
                                               'business_date':today, 'business_type':'normal', 'notes':name})
        posting('finished-outbounds', {'product_id':product['id'], 'quantity':'6', 'unit':'盒',
                                     'business_date':today, 'business_type':'normal'}, 409)
        out = posting('finished-outbounds', {'product_id':product['id'], 'quantity':'2', 'unit':'盒',
                                     'business_date':today, 'business_type':'normal', 'notes':name})
        assert Decimal(request('GET', '/api/finished-stock/' + str(product['id']))['quantity']) == Decimal('3.123456')
        posting('stock-documents/' + str(out['id']) + '/reverse', {'reason':name})
        assert Decimal(request('GET', '/api/finished-stock/' + str(product['id']))['quantity']) == Decimal('5.123456')
        material = request('POST', '/api/materials', {'code':'INSTALL_MATERIAL', 'name':name, 'unit':'kg'})
        def formula(version):
            return request('POST', '/api/boms', {'product_id':product['id'], 'version':version, 'status':'active',
                'items':[{'material_id':material['id'], 'quantity_per_unit':'0.000001', 'unit':'kg', 'sort_order':0}]})
        unused = formula(1)
        unused_path = '/api/boms/' + str(unused['id'])
        token_before = token
        token = None
        request('DELETE', unused_path, expected=401)
        token = token_before
        request('DELETE', unused_path)
        request('GET', unused_path, expected=404)
        request('DELETE', unused_path, expected=404)
        protected = formula(2)
        order = request('POST', '/api/production-orders', {'finished_product_id':product['id'], 'planned_quantity':'1'})
        order_path = '/api/production-orders/' + str(order['id'])
        snapshot = request('GET', order_path)
        request('DELETE', '/api/boms/' + str(protected['id']), expected=409)
        request('POST', order_path + '/cancel')
        cancelled_snapshot = request('GET', order_path)
        request('DELETE', '/api/boms/' + str(protected['id']), expected=409)
        assert request('GET', order_path) == cancelled_snapshot
        assert cancelled_snapshot['materials'] == snapshot['materials']
        with db() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT status, is_active, deleted_at, active_product_id FROM bom_headers WHERE id=%s', (unused['id'],))
                status, active, deleted, active_product = cursor.fetchone()
                assert status == 'archived' and not active and deleted is not None and active_product is None
                cursor.execute('SELECT quantity_per_unit FROM bom_items WHERE bom_id=%s', (unused['id'],))
                assert cursor.fetchone()[0] == Decimal('0.000001')
                cursor.execute("SELECT COUNT(*) FROM operation_logs WHERE action='delete_bom'")
                assert cursor.fetchone()[0] == 1
                cursor.execute('SELECT COUNT(*) FROM inventory_transactions')
                assert cursor.fetchone()[0] == 3
        report['checks'].append('packaged BOM deletion: anonymous refused, unused active version soft-deleted with items/audit preserved, next version enabled, referenced and cancelled-order versions refused, production snapshot and stock unchanged')
        with db('wms_app') as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE inventory_transactions SET change_quantity=change_quantity WHERE 1=0')
                raise AssertionError('Application account can modify immutable ledger')
            except pymysql.err.OperationalError as exc:
                assert exc.args[0] == 1142
        diagnostic = "import sys,json;sys.path.insert(0,sys.argv[1]);import runtime;runtime.environment(json.loads((runtime.ROOT/'data/config.json').read_text()));from alembic.config import Config;from alembic import command;cfg=Config(str(runtime.BACKEND/'alembic.ini'));command.current(cfg);command.heads(cfg);command.check(cfg);from scripts.check_inventory import main;sys.exit(main())"
        result = subprocess.run([str(root/'python/python.exe'), '-I', '-c', diagnostic, str(root/'controller')],
                                capture_output=True, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, 'packaged migration/reconciliation failed'
        (args.report.parent / 'packaged-migration-reconciliation.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        report['checks'].append('actual API login, six-place quantity, insufficient outbound rollback, reversal, immutable ledger permissions, nonempty reconciliation')
        backup = controller.backup()
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup.with_suffix('.sql.sha256').read_text().split()[0]
        controller.stop()
        controller = Controller(root, {})
        token = request('POST', '/api/auth/login', credentials)['access_token']
        assert Decimal(request('GET', '/api/finished-stock/' + str(product['id']))['quantity']) == Decimal('5.123456')
        request('GET', unused_path, expected=404)
        assert request('GET', order_path) == cancelled_snapshot
        report['checks'].append('clean stop/restart keeps account, document history and inventory')
        controller.stop(); controller = None
        restore_root = args.report.parent / 'restore-check'
        if restore_root.exists():
            raise RuntimeError('restore directory already exists')
        restore_root.mkdir()
        mysql = root / 'mysql'
        restore_data = restore_root / 'mysql'
        subprocess.run([str(mysql/'bin/mysqld.exe'), '--no-defaults', '--initialize-insecure',
                        '--basedir='+str(mysql), '--datadir='+str(restore_data),
                        '--log-error='+str(restore_root/'initialize.log')], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        with __import__('socket').socket() as check:
            check.bind(('127.0.0.1', 33598))
        restored = subprocess.Popen([str(mysql/'bin/mysqld.exe'), '--no-defaults', '--basedir='+str(mysql),
            '--datadir='+str(restore_data), '--bind-address=127.0.0.1', '--port=33598', '--mysqlx=0',
            '--log-error='+str(restore_root/'mysql.log')], creationflags=subprocess.CREATE_NO_WINDOW)
        connection = None
        try:
            for _ in range(120):
                try:
                    connection = pymysql.connect(host='127.0.0.1', port=33598, user='root', autocommit=True)
                    break
                except pymysql.Error:
                    time.sleep(.5)
            assert connection is not None
            with connection.cursor() as cursor:
                cursor.execute('SELECT @@datadir'); assert Path(cursor.fetchone()[0]).resolve() == restore_data
                cursor.execute('CREATE DATABASE warehouse_standalone')
            with backup.open('rb') as source:
                subprocess.run([str(mysql/'bin/mysql.exe'), '--no-defaults', '--host=127.0.0.1', '--port=33598',
                                '--user=root', 'warehouse_standalone'], stdin=source, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            with connection.cursor() as cursor:
                cursor.execute('SELECT version_num FROM warehouse_standalone.alembic_version'); assert cursor.fetchone()[0] == expected_revision
                cursor.execute('SELECT remaining_quantity FROM warehouse_standalone.finished_batches'); assert cursor.fetchone()[0] == Decimal('5.123456')
                cursor.execute('SELECT COUNT(*) FROM warehouse_standalone.inventory_transactions'); assert cursor.fetchone()[0] == 3
                cursor.execute('SELECT COUNT(*) FROM warehouse_standalone.stock_documents'); assert cursor.fetchone()[0] == 3
                cursor.execute('SELECT COUNT(*) FROM warehouse_standalone.users'); assert cursor.fetchone()[0] == 1
                cursor.execute('SELECT status, deleted_at FROM warehouse_standalone.bom_headers WHERE id=%s', (unused['id'],))
                restored_status, restored_deleted = cursor.fetchone()
                assert restored_status == 'archived' and restored_deleted is not None
                cursor.execute('SELECT COUNT(*) FROM warehouse_standalone.bom_items'); assert cursor.fetchone()[0] == 2
                cursor.execute('SELECT status FROM warehouse_standalone.production_orders WHERE id=%s', (order['id'],))
                assert cursor.fetchone()[0] == 'cancelled'
                cursor.execute("SELECT COUNT(*) FROM warehouse_standalone.operation_logs WHERE action='delete_bom'")
                assert cursor.fetchone()[0] == 1
            report['checks'].append(f'SQL backup checksum and actual restore into another empty MySQL instance: {expected_revision}, 3 documents, 3 ledger entries, 5.123456 boxes, 1 admin')
            report['checks'].append('backup restore preserves soft-deleted BOM/items/audit and protected cancelled production order')
        finally:
            if connection is not None:
                with connection.cursor() as cursor:
                    cursor.execute('SHUTDOWN')
                connection.close()
                restored.wait(timeout=30)
        report['ok'] = True
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if controller is not None:
            controller.stop()


if __name__ == '__main__':
    main()
