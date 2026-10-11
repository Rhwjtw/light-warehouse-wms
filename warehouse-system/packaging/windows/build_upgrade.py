"""Build an update zip from a verified full package or an isolated frontend build."""
import argparse, hashlib, json, os, shutil, subprocess, zipfile
from pathlib import Path
APP=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--frontend',type=Path)
    p.add_argument('--from-package',type=Path)
    p.add_argument('--baseline',required=True)
    args=p.parse_args()
    target=args.output.resolve()
    if not target.is_relative_to(APP/'.runtime') or target.exists():
        raise RuntimeError('Use a new .runtime build directory')
    target.mkdir(parents=True)
    if args.from_package:
        source=args.from_package.resolve()
        if not source.is_relative_to(APP/'.runtime'):
            raise RuntimeError('Source package must be isolated')
        manifest=json.loads((source/'package-manifest.json').read_text(encoding='utf-8'))
        if manifest['version']!='1.3.2' or manifest['business_baseline']!=args.baseline:
            raise RuntimeError('Full package version/baseline mismatch')
        for name in ('backend','frontend','controller'):
            shutil.copytree(source/name,target/name)
        for name in ('轻仓WMS.exe','使用说明.txt'):
            shutil.copy2(source/name,target/name)
        for file in target.rglob('*'):
            if file.is_file():
                name=file.relative_to(target).as_posix()
                if hashlib.sha256(file.read_bytes()).hexdigest()!=manifest['files'][name]:
                    raise RuntimeError('Source package SHA mismatch')
    else:
        if not args.frontend:
            raise RuntimeError('Frontend or source package is required')
        backend=target/'backend'
        shutil.copytree(APP/'backend/app',backend/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copytree(APP/'backend/alembic',backend/'alembic',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in ('alembic.ini','requirements-lock.txt'):
            shutil.copy2(APP/'backend'/name,backend/name)
        (backend/'scripts').mkdir()
        for name in ('init_admin.py','check_inventory.py'):
            shutil.copy2(APP/'backend/scripts'/name,backend/'scripts'/name)
        shutil.copytree(args.frontend,target/'frontend/dist')
        (target/'controller').mkdir()
        shutil.copy2(Path(__file__).parent/'runtime.py',target/'controller/runtime.py')
        shutil.copy2(Path(__file__).parent/'升级说明.txt',target/'使用说明.txt')
        csc=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        subprocess.run([str(csc),'/nologo','/utf8output','/target:winexe','/platform:x64','/optimize+',
            '/win32icon:'+str(Path(__file__).parent/'assets/wms.ico'),
            '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll',
            '/out:'+str(target/'轻仓WMS.exe'),str(Path(__file__).parent/'Client.cs')],check=True)
    files={f.relative_to(target).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in target.rglob('*') if f.is_file()}
    assert all(not k.startswith(('data/','python/','mysql/')) and not Path(k).name.startswith('.env') for k in files)
    profile=manifest.get('frontend_profile') if args.from_package else None
    compatibility=json.loads((Path(__file__).parent/'profiles/compatible-v13.json').read_text(encoding='utf-8'))
    manifest=dict(version='1.3.2',revision='0015',business_baseline=args.baseline,files=files,
        frontend_profile=profile,compatible_v13=compatibility)
    archive=target.parent/'wms-upgrade.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr('upgrade-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        for name in files: output.write(target/name,name)
    print('Upgrade payload files=',len(files),'zip bytes=',archive.stat().st_size)
if __name__=='__main__': main()
