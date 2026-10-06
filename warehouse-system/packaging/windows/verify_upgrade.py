"""Real EXE upgrade acceptance; only a fresh installation under this repo .runtime."""
import argparse
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4
import zipfile
import pymysql
from verify_package import Controller
import upgrade

APP=Path(__file__).resolve().parents[2]
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--installer',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    args.installer=args.installer.resolve()
    args.report=args.report.resolve()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    root=args.root.resolve()
    if not root.is_relative_to(APP/'.runtime') or (root/'data').exists():
        raise RuntimeError('Use a fresh .runtime V1.2 installation')
    report={'checks':[]}
    credentials={'username':'upgradeadmin','password':'升级验收密码'+secrets.token_urlsafe(24)}
    controller=None
    token=None
    cfg=None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(method,path,body=None,expected=200):
        headers={'Content-Type':'application/json'}
        if token: headers['Authorization']='Bearer '+token
        req=urllib.request.Request(controller.url+path,data=json.dumps(body).encode() if body is not None else None,
            headers=headers,method=method)
        try: response=opener.open(req,timeout=15)
        except urllib.error.HTTPError as exc: response=exc
        with response:
            actual=response.status; result=json.load(response)
        assert actual==expected,(method,path,actual,expected,result.get('code'))
        return result.get('data')
    def post(path,body):
        return request('POST',path,{'request_id':uuid4().hex,**body})
    def setup(label,expected):
        result=subprocess.run([str(args.installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',
            '/DIR='+str(root),'/LOG='+str(args.report.parent/(label+'.log'))],
            creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode==expected,(label,result.returncode)
    try:
        controller=Controller(root,credentials)
        cfg=json.loads((root/'data/config.json').read_text())
        cfg_hash=upgrade.digest(root/'data/config.json')
        token=request('POST','/api/auth/login',credentials)['access_token']
        roles=request('GET','/api/roles')
        for role in roles:
            if role['code']!='admin':
                request('POST','/api/users',{'username':'upgrade_'+role['code'],
                    'display_name':'升级验收'+role['name'],'password':credentials['password'],'role_id':role['id']})
        product=request('POST','/api/products',{'code':'UPGRADE_PRODUCT','name':'升级保留成品','unit':'盒'})
        material=request('POST','/api/materials',{'code':'UPGRADE_MATERIAL','name':'升级保留原料','unit':'kg'})
        supplier=request('POST','/api/suppliers',{'code':'UPGRADE_SUPPLIER','name':'升级保留供应商'})
        location=request('POST','/api/locations',{'name':'升级保留库位'})
        post('/api/finished-receipts',{'product_id':product['id'],'quantity':'5.123456','unit':'盒',
            'business_date':date.today().isoformat(),'business_type':'normal','notes':'升级前验收'})
        out=post('/api/finished-outbounds',{'product_id':product['id'],'quantity':'2','unit':'盒',
            'business_date':date.today().isoformat(),'business_type':'normal'})
        post('/api/stock-documents/'+str(out['id'])+'/reverse',{'reason':'升级前冲销验收'})
        receipt=post('/api/material-receipts',{'material_id':material['id'],'batch_no':'UPGRADE-LEGACY',
            'quantity':'250','unit':'kg','production_date':(date.today()-timedelta(days=1)).isoformat(),
            'expiry_date':(date.today()+timedelta(days=365)).isoformat(),'supplier_id':supplier['id']})
        with upgrade.connection(root,cfg) as con:
            with con.cursor() as c:
                c.execute('SELECT id FROM material_movements WHERE document_id=%s',(receipt['id'],))
                movement=c.fetchone()[0]
        post('/api/material-putaways',{'receipt_movement_id':movement,'location_id':location['id'],'quantity':'250'})
        with upgrade.connection(root,cfg) as con:
            with con.cursor() as c:
                c.execute("SELECT id FROM material_batches WHERE stock_status='shelved'")
                batch=c.fetchone()[0]
        request('POST','/api/boms',{'product_id':product['id'],'version':1,'status':'active',
            'items':[{'material_id':material['id'],'quantity_per_unit':'0.02','unit':'kg'}]})
        order=request('POST','/api/production-orders',{'finished_product_id':product['id'],'planned_quantity':'10000'})
        order_path='/api/production-orders/'+str(order['id'])
        material_id=order['materials'][0]['id']
        post(order_path+'/issue',{'order_material_id':material_id,'unit':'kg','allocations':[{'batch_id':batch,'quantity':'40'}]})
        post('/api/material-outbounds',{'material_id':material['id'],'batch_id':batch,'quantity':'2','unit':'kg','notes':'旧版出库'})
        old_order=request('GET',order_path)
        setup('refuse-running',7)
        assert upgrade.digest(root/'data/config.json')==cfg_hash
        controller.stop(); controller=None
        # Corrupt a copy of the payload. Refusal must precede any database or program change.
        tampered=args.report.parent/'tampered.zip'
        with zipfile.ZipFile(args.installer.resolve().parent.parent/'wms-upgrade.zip') as src, zipfile.ZipFile(tampered,'w') as dst:
            for name in src.namelist():
                data=src.read(name)
                dst.writestr(name,data+b'\n# tamper' if name=='controller/runtime.py' else data)
        state=upgrade.digest(root/'package-manifest.json')
        result=subprocess.run([str(root/'python/python.exe'),'-I',str(APP/'packaging/windows/upgrade.py'),
            '--root',str(root),'--payload',str(tampered)],creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode==1 and upgrade.digest(root/'package-manifest.json')==state
        report['checks'].append('running installation and tampered payload refused without changing configuration/program')
        setup('upgrade-v13',0)
        sessions=sorted((root/'data/upgrade-backups').glob('*/report.json'))
        successful=[json.loads(f.read_text()) for f in sessions if json.loads(f.read_text()).get('historical_rows_unchanged')]
        assert len(successful)==1
        preserved=successful[0]
        assert preserved['before']==preserved['after'] and preserved['config_unchanged'] and preserved['grants_unchanged']
        report['preservation']=preserved['before']
        report['checks'].append('actual EXE upgrade 0014->0015; all 26 historical tables/rows, config and grants identical')
        controller=Controller(root,{})
        token=request('POST','/api/auth/login',credentials)['access_token']
        assert request('GET',order_path)==old_order
        assert Decimal(request('GET','/api/finished-stock/'+str(product['id']))['quantity'])==Decimal('5.123456')
        for role in roles:
            login={'username':credentials['username'] if role['code']=='admin' else 'upgrade_'+role['code'],
                'password':credentials['password']}
            assert request('POST','/api/auth/login',login)['access_token']
        for path in ('/','/login','/orders','/material-outbound','/boms'):
            with opener.open(controller.url+path) as response:
                assert hashlib.sha256(response.read()).hexdigest()==upgrade.digest(root/'frontend/dist/index.html')
        for amount in ('100','60'):
            post(order_path+'/issue',{'order_material_id':material_id,'unit':'kg','allocations':[{'batch_id':batch,'quantity':amount}]})
        assert request('GET',order_path)['materials'][0]['issued_quantity']=='200'
        for purpose,quantity in (('supplier_return','2'),('scrap','3')):
            disposition=post('/api/material-outbounds',{'material_id':material['id'],'batch_id':batch,
                'quantity':quantity,'unit':'kg','purpose':purpose,'notes':'V1.3升级后验收'})
            assert disposition['purpose']==purpose
        post('/api/stock-documents/'+str(disposition['id'])+'/reverse',{'reason':'升级后处置冲销验收'})
        log=args.report.parent/'upgraded-reconciliation.log'
        upgrade.diagnose(root,cfg,root/'backend',log)
        report['checks'].append('original passwords/five roles/stock/order retained; V1.3 routes, cumulative 200kg issues, return/scrap/reversal and nonempty reconciliation pass')
        controller.stop(); controller=None
        marker=root/'data/upgrade-in-progress.json'
        marker.write_text('{}')
        result=subprocess.run([str(root/'python/python.exe'),'-I',str(root/'controller/runtime.py'),'run',str(root/'data')],
            input='{}\n',capture_output=True,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode!=0 and json.loads(result.stdout)['event']=='error'
        assert marker.exists() and upgrade.digest(root/'data/config.json')==cfg_hash
        with __import__('socket').socket() as listener: listener.bind(('127.0.0.1',cfg['mysql_port']))
        marker.unlink()
        setup('repeat-upgrade',0)
        assert upgrade.digest(root/'data/config.json')==cfg_hash
        report['checks'].append('unfinished upgrade blocks startup; repeated successful upgrade is idempotent')
        # Restore the pre-upgrade SQL into a second independent MySQL, never into the upgraded data.
        backup=Path(preserved['backup'])/'before-upgrade.sql'
        assert upgrade.digest(backup)==backup.with_suffix('.sql.sha256').read_text().split()[0]
        restore=args.report.parent/'restore'
        data=restore/'data/mysql'
        data.parent.mkdir(parents=True)
        mysql=root/'mysql'
        subprocess.run([str(mysql/'bin/mysqld.exe'),'--no-defaults','--initialize-insecure',
            '--basedir='+str(mysql),'--datadir='+str(data),'--log-error='+str(restore/'init.log')],
            check=True,creationflags=subprocess.CREATE_NO_WINDOW)
        restored=subprocess.Popen([str(mysql/'bin/mysqld.exe'),'--no-defaults','--basedir='+str(mysql),
            '--datadir='+str(data),'--bind-address=127.0.0.1','--port=33599','--mysqlx=0',
            '--log-error='+str(restore/'mysql.log')],creationflags=subprocess.CREATE_NO_WINDOW)
        con=None
        try:
            for _ in range(120):
                try:
                    con=pymysql.connect(host='127.0.0.1',port=33599,user='root',autocommit=True); break
                except pymysql.Error: time.sleep(.5)
            assert con is not None
            with con.cursor() as c:
                c.execute('SELECT @@datadir,@@server_uuid')
                directory,identity=c.fetchone()
                assert Path(directory).resolve()==data
                c.execute('CREATE DATABASE warehouse_standalone')
            with backup.open('rb') as sql:
                subprocess.run([str(mysql/'bin/mysql.exe'),'--no-defaults','--host=127.0.0.1','--port=33599',
                    '--user=root','warehouse_standalone'],stdin=sql,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
            assert upgrade.snapshot(restore,dict(mysql_port=33599,root_password='',server_uuid=identity))==preserved['before']
            with con.cursor() as c:
                c.execute('SELECT version_num FROM warehouse_standalone.alembic_version')
                assert c.fetchone()[0]=='0014'
        finally:
            if con is not None:
                with con.cursor() as c: c.execute('SHUTDOWN')
                con.close(); restored.wait(timeout=60)
        report['checks'].append('automatic backup SHA and actual independent restore preserve every old table/row at 0014')
        report['ok']=True
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'checks':report['checks']},ensure_ascii=False))
    finally:
        if controller is not None: controller.stop()
if __name__=='__main__': main()
