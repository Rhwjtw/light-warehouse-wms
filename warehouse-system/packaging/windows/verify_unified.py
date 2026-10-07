"""Real unified-installer acceptance. Never run against a user's installation."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.request
import winreg
from uuid import uuid4
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parent))
from verify_package import Controller
APP=Path(__file__).resolve().parents[2]
KEY=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{924D0ED8-98CF-4BBF-B1F2-EE1E1D695B72}_is1'
NO_WINDOW=subprocess.CREATE_NO_WINDOW

def registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            return Path(winreg.QueryValueEx(key,'InstallLocation')[0]).resolve()
    except FileNotFoundError: return None

def fault_worker(root, archive, mode):
    assert root.resolve().is_relative_to(APP/'.runtime')
    assert Path(sys.executable).resolve()==root.resolve()/'python/python.exe'
    spec=importlib.util.spec_from_file_location('worker',Path(__file__).parent/'upgrade.py')
    worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    normal=worker.diagnose
    def interrupted(installed,cfg,backend,log,migrate=False):
        if migrate:
            if mode=='after_migration':
                normal(installed,cfg,backend,log,migrate=True)
            elif mode=='partial_ddl':
                with worker.connection(installed,cfg) as con:
                    with con.cursor() as c:
                        c.execute('ALTER TABLE stock_documents ADD COLUMN material_outbound_purpose VARCHAR(20) NULL')
            raise worker.Refusal(5)
        return normal(installed,cfg,backend,log,migrate=False)
    worker.diagnose=interrupted
    try: worker.perform(root.resolve(),archive.resolve())
    except worker.Refusal as exc: return exc.code
    raise AssertionError('Expected interrupted update')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--installer',type=Path)
    p.add_argument('--legacy-installer',type=Path)
    p.add_argument('--legacy-upgrader',type=Path)
    p.add_argument('--previous-unified-installer',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--fault-root',type=Path)
    p.add_argument('--fault-payload',type=Path)
    p.add_argument('--fault',choices=('before_migration','after_migration','partial_ddl'))
    args=p.parse_args()
    if args.fault_root:
        return fault_worker(args.fault_root,args.fault_payload,args.fault)
    out=args.output.resolve()
    assert out.is_relative_to(APP/'.runtime') and not out.exists()
    assert registered() is None,'An independent installation is registered; do not test over it'
    out.mkdir(parents=True)
    installer=args.installer.resolve()
    legacy=args.legacy_installer.resolve()
    payload=installer.parent.parent/'wms-upgrade.zip'
    # Final output and initial output both use the same parent payload.
    assert payload.is_file()
    assert all(x.is_relative_to(APP/'.runtime') for x in (installer,legacy,payload))
    report=dict(ok=False,checks=[],roots=[])
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def execute(command,log=None,expected=0):
        with (out/log).open('wb') if log else open(os.devnull,'wb') as sink:
            r=subprocess.run([str(x) for x in command],stdout=sink,stderr=subprocess.STDOUT,creationflags=NO_WINDOW)
        assert r.returncode==expected,(log,r.returncode,expected)
    def install(exe,root,label,expected=0,auto=False,desktop=False):
        command=[exe,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',
            '/TASKS=desktopicon' if desktop else '/TASKS=""','/LOG='+str(out/(label+'.log'))]
        if not auto: command.append('/DIR='+str(root))
        execute(command,expected=expected)
        if expected==0: assert registered()==root
    def uninstall(root,label):
        assert registered()==root
        execute([root/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LOG='+str(out/(label+'.log'))])
        assert registered() is None
        assert (root/'data/config.json').is_file() and (root/'data/mysql/auto.cnf').is_file()
    def fixture(name):
        root=out/name
        assert not root.exists()
        install(legacy,root,name+'-install')
        report['roots'].append(str(root))
        return root
    def seed(root):
        credentials=dict(username='unifiedadmin',password='独立安装验收密码'+secrets.token_urlsafe(24))
        c=Controller(root,credentials)
        def request(path,body,token=None):
            headers={'Content-Type':'application/json'}
            if token: headers['Authorization']='Bearer '+token
            req=urllib.request.Request(c.url+path,data=json.dumps(body).encode(),headers=headers,method='POST')
            with opener.open(req,timeout=20) as response: return json.load(response)['data']
        token=request('/api/auth/login',credentials)['access_token']
        product=request('/api/products',dict(code='UNIFIED-'+uuid4().hex[:8],name='通用安装数据保留验收',unit='盒'),token)
        request('/api/finished-receipts',dict(product_id=product['id'],quantity='5.123456',unit='盒',business_date=date.today().isoformat(),business_type='normal',request_id=uuid4().hex),token)
        c.stop()
        return credentials,product['id']
    def login_check(root,credentials,product):
        c=Controller(root,{})
        try:
            req=urllib.request.Request(c.url+'/api/auth/login',data=json.dumps(credentials).encode(),headers={'Content-Type':'application/json'},method='POST')
            with opener.open(req,timeout=20) as r: token=json.load(r)['data']['access_token']
            req=urllib.request.Request(c.url+'/api/finished-stock/'+str(product),headers={'Authorization':'Bearer '+token})
            with opener.open(req,timeout=20) as r: assert json.load(r)['data']['quantity']=='5.123456'
        finally: c.stop()
    def summaries(root):
        return [json.loads(x.read_text(encoding='utf-8')) for x in (root/'data/upgrade-backups').glob('*/report.json')]
    # Fresh full installer: 3172 file hashes, initialization, inventory, BOM and restore.
    fresh=out/'fresh'
    shortcut=Path(os.environ['USERPROFILE'])/'Desktop/轻仓 WMS 独立版.lnk'
    assert not shortcut.exists(),'Preserve an existing desktop shortcut'
    install(installer,fresh,'fresh-install',desktop=True)
    script="$s=(New-Object -ComObject WScript.Shell).CreateShortcut('"+str(shortcut)+"'); if($s.TargetPath -ne '"+str(fresh/'轻仓WMS.exe')+"' -or $s.WorkingDirectory -ne '"+str(fresh)+"' -or $s.IconLocation -notin @('',',0')){throw 'Desktop shortcut mismatch'}; $v=(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{924D0ED8-98CF-4BBF-B1F2-EE1E1D695B72}_is1').DisplayVersion; if($v -ne '1.3.1'){throw 'Installer version mismatch'}"
    execute(['powershell.exe','-NoProfile','-Command',script],'desktop-shortcut-check.log')
    report['checks'].append('actual desktop shortcut targets the icon-embedded EXE; installation registers version 1.3.1')
    report['roots'].append(str(fresh))
    execute([APP/'backend/.venv/Scripts/python.exe',Path(__file__).parent/'verify_package.py',
        '--install-root',fresh,'--report',out/'fresh-acceptance/report.json'],'fresh-acceptance-run.log')
    assert json.loads((out/'fresh-acceptance/report.json').read_text())['ok']
    install(installer,fresh,'fresh-repeat-auto',auto=True)
    assert any(x.get('already_current') for x in summaries(fresh))
    report['checks'].append('full EXE fresh install, precise inventory/BOM/permissions/SQL restore and repeat auto-location pass')
    # Resource inspection is read-only. Desktop shortcut creation is covered by installer entries.
    extract="Add-Type -AssemblyName System.Drawing; $i=[Drawing.Icon]::ExtractAssociatedIcon('"+str(fresh/'轻仓WMS.exe')+"'); $i.ToBitmap().Save('"+str(out/'program-icon.png')+"',[Drawing.Imaging.ImageFormat]::Png); $s=[Drawing.Icon]::ExtractAssociatedIcon('"+str(installer)+"'); $s.ToBitmap().Save('"+str(out/'installer-icon.png')+"',[Drawing.Imaging.ImageFormat]::Png)"
    execute(['powershell.exe','-NoProfile','-Command',extract],'icon-resource.log')
    uninstall(fresh,'fresh-uninstall')
    assert not shortcut.exists()
    # Full regression of ordinary V1.2 upgrade, passwords/five roles, business and backup restore.
    root=fixture('ordinary-upgrade')
    execute([APP/'backend/.venv/Scripts/python.exe',Path(__file__).parent/'verify_upgrade.py',
        '--root',root,'--installer',installer,'--report',out/'ordinary-acceptance/report.json'],'ordinary-acceptance-run.log')
    assert json.loads((out/'ordinary-acceptance/report.json').read_text())['ok']
    report['checks'].append('official V1.2 -> unified EXE V1.3 preserves all 26 tables/config/grants and five original roles; new business and restore pass')
    uninstall(root,'ordinary-uninstall')
    # Verify the previous full installer can update real data to the changed desktop frontend.
    if args.previous_unified_installer:
        previous=args.previous_unified_installer.resolve()
        assert previous.is_relative_to(APP/'.runtime')
        root=out/'previous-unified'
        install(previous,root,'previous-unified-install')
        report['roots'].append(str(root))
        credentials,product=seed(root)
        manifest_path=root/'package-manifest.json'
        original_manifest=manifest_path.read_bytes()
        original=json.loads(original_manifest)
        assert original['version']=='1.3.0'
        cfg_hash=hashlib.sha256((root/'data/config.json').read_bytes()).hexdigest()
        # A forged local inventory must not bypass the approved published-source inventory.
        index=root/'frontend/dist/index.html'
        original_index=index.read_bytes()
        index.write_bytes(original_index+b'\\n<!-- unapproved local change -->')
        forged=json.loads(original_manifest)
        forged['files']['frontend/dist/index.html']=hashlib.sha256(index.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(forged),encoding='utf-8')
        forged_bytes=manifest_path.read_bytes()
        install(installer,root,'previous-unified-forged-source',auto=True,expected=7)
        assert manifest_path.read_bytes()==forged_bytes and index.read_bytes()!=original_index
        assert hashlib.sha256((root/'data/config.json').read_bytes()).hexdigest()==cfg_hash
        assert not (root/'data/upgrade-in-progress.json').exists()
        index.write_bytes(original_index); manifest_path.write_bytes(original_manifest)
        install(installer,root,'previous-unified-update-auto',auto=True)
        updated=json.loads(manifest_path.read_text(encoding='utf-8'))
        assert updated['version']=='1.3.1' and updated['frontend_profile']['name']=='desktop-quantity-v13'
        assert updated['files']['frontend/dist/index.html']!=original['files']['frontend/dist/index.html']
        old_backend={k:v for k,v in original['files'].items() if k.startswith('backend/')}
        new_backend={k:v for k,v in updated['files'].items() if k.startswith('backend/')}
        assert old_backend==new_backend
        success=[x for x in summaries(root) if x.get('source_revision')=='0015' and x.get('historical_rows_unchanged')]
        assert success and success[-1]['before']==success[-1]['after']
        login_check(root,credentials,product)
        install(installer,root,'previous-unified-repeat-auto',auto=True)
        assert any(x.get('already_current') for x in summaries(root))
        report['checks'].append('previous full V1.3 -> V1.3.1 finds original directory, changes only approved desktop frontend, retains account/5.123456 stock/config/grants; repeat is idempotent; forged source inventory refused')
        uninstall(root,'previous-unified-uninstall')
    # A historical V1.3 thin updater creates non-NULL purposes; the full installer must retain them.
    if args.legacy_upgrader:
        root=fixture('existing-v13')
        execute([APP/'backend/.venv/Scripts/python.exe',Path(__file__).parent/'verify_upgrade.py',
            '--root',root,'--installer',args.legacy_upgrader.resolve(),'--report',out/'existing-v13-acceptance/report.json'],'existing-v13-run.log')
        cfg_hash=hashlib.sha256((root/'data/config.json').read_bytes()).hexdigest()
        install(installer,root,'existing-v13-auto-upgrade',auto=True)
        success=[x for x in summaries(root) if x.get('source_revision')=='0015' and x.get('historical_rows_unchanged')]
        assert success and success[-1]['before']==success[-1]['after']
        assert 'material_outbound_purpose' in success[-1]['before']['stock_documents']['columns']
        assert hashlib.sha256((root/'data/config.json').read_bytes()).hexdigest()==cfg_hash
        report['checks'].append('earlier V1.3 installation including return/scrap facts updates automatically without migration/history changes')
        uninstall(root,'existing-v13-uninstall')
    # Real MySQL/program interruptions, followed by the actual EXE WITHOUT /DIR.
    for mode in ('before_migration','after_migration','partial_ddl'):
        root=fixture(mode)
        credentials,product=seed(root)
        cfg_hash=hashlib.sha256((root/'data/config.json').read_bytes()).hexdigest()
        execute([root/'python/python.exe','-I',Path(__file__),'--fault-root',root,'--fault-payload',payload,'--fault',mode],mode+'-fault.log',expected=5)
        marker=root/'data/upgrade-in-progress.json'
        assert marker.is_file()
        previous=marker.read_bytes()
        install(installer,root,mode+'-auto-resume',expected=7 if mode=='partial_ddl' else 0,auto=True)
        assert hashlib.sha256((root/'data/config.json').read_bytes()).hexdigest()==cfg_hash
        if mode=='partial_ddl':
            assert marker.read_bytes()==previous
            failure=[x for x in summaries(root) if x.get('resumed') and not x.get('ok')]
            assert failure and failure[-1]['error_code']==8
            report['checks'].append('partial committed MySQL DDL safely refuses automatic replay; marker and original config/data remain')
        else:
            assert not marker.exists()
            successful=[x for x in summaries(root) if x.get('resumed') and x.get('ok')]
            assert successful and successful[-1]['before']==successful[-1]['after']
            login_check(root,credentials,product)
            report['checks'].append(mode+': automatic registry location and continuation preserve every old row, grants, original password and 5.123456 inventory')
        uninstall(root,mode+'-uninstall')
    wrong=out/'unrelated-files'; wrong.mkdir()
    sentinel=wrong/'keep.txt'; sentinel.write_text('isolated data refusal')
    install(installer,wrong,'wrong-directory',expected=7)
    assert sentinel.read_text()=='isolated data refusal' and not (wrong/'data').exists()
    report['checks'].append('unrelated nonempty directory refused unchanged; all real uninstall runs retain data')
    report['ok']=True
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0
if __name__=='__main__': sys.exit(main())
