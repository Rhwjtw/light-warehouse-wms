"""Stage redistributable files only; never read the live backend .env or MySQL data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

APP = Path(__file__).resolve().parents[2]


def copy_tree(source, target, ignore=()):
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pdb', *ignore))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--mysql', type=Path, required=True)
    parser.add_argument('--redist', type=Path, required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    target = args.output.resolve()
    if not target.is_relative_to(APP / '.runtime') or target.exists():
        raise RuntimeError('Package staging must use a new directory under .runtime')
    target.mkdir(parents=True)
    source = Path(__file__).parent
    copy_tree(APP / 'backend' / 'app', target / 'backend' / 'app')
    copy_tree(APP / 'backend' / 'alembic', target / 'backend' / 'alembic')
    for name in ('alembic.ini', 'requirements-lock.txt'):
        shutil.copy2(APP / 'backend' / name, target / 'backend' / name)
    (target / 'backend' / 'scripts').mkdir()
    for name in ('init_admin.py', 'check_inventory.py'):
        shutil.copy2(APP / 'backend' / 'scripts' / name, target / 'backend' / 'scripts' / name)
    copy_tree(args.frontend.resolve(), target / 'frontend' / 'dist')
    interpreter = Path(sys.base_prefix)
    (target / 'python').mkdir()
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        shutil.copy2(interpreter / name, target / 'python' / name)
    copy_tree(interpreter / 'DLLs', target / 'python' / 'DLLs')
    copy_tree(interpreter / 'Lib', target / 'python' / 'Lib', ('site-packages', 'test', 'tests', 'idlelib', 'tkinter', 'ensurepip'))
    copy_tree(APP / 'backend' / '.venv' / 'Lib' / 'site-packages', target / 'python' / 'Lib' / 'site-packages',
              ('pip', 'pip-*.dist-info'))
    (target / 'mysql' / 'bin').mkdir(parents=True)
    for name in ('mysqld.exe', 'mysql.exe', 'mysqladmin.exe', 'mysqldump.exe'):
        shutil.copy2(args.mysql / 'bin' / name, target / 'mysql' / 'bin' / name)
    for library in (args.mysql / 'bin').glob('*.dll'):
        if 'debug' not in library.name:
            shutil.copy2(library, target / 'mysql' / 'bin' / library.name)
    copy_tree(args.mysql / 'lib', target / 'mysql' / 'lib', ('*.lib', '*.pdb', 'debug'))
    copy_tree(args.mysql / 'share', target / 'mysql' / 'share')
    for name in ('LICENSE', 'README'):
        shutil.copy2(args.mysql / name, target / 'mysql' / name)
    copy_tree(args.mysql / 'docs', target / 'mysql' / 'docs')
    (target / 'controller').mkdir()
    shutil.copy2(source / 'runtime.py', target / 'controller' / 'runtime.py')
    shutil.copy2(source / '安装说明.txt', target / '使用说明.txt')
    shutil.copy2(args.redist, target / 'VC_redist.x64.exe')
    compiler = Path(__import__('os').environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/utf8output', '/target:winexe', '/platform:x64', '/optimize+',
                    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                    '/reference:System.Web.Extensions.dll', '/out:' + str(target / '轻仓WMS.exe'),
                    str(source / 'Client.cs')], check=True)
    manifest = {}
    for file in target.rglob('*'):
        if not file.is_file():
            continue
        relative = file.relative_to(target).as_posix()
        public_certificate = relative == 'python/Lib/site-packages/certifi/cacert.pem'
        if file.name.startswith('.env') or (file.suffix.lower() in {'.sql', '.cnf', '.pem', '.key'} and not public_certificate):
            raise RuntimeError('Forbidden configuration or credential file in package: ' + relative)
        if relative != 'VC_redist.x64.exe':
            manifest[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    (target / 'package-manifest.json').write_text(json.dumps(dict(format=1, product='LightWarehouseWMS',
        version='1.2.0', business_baseline=args.baseline, mysql='8.4.9', python='3.12.14',
        prerequisite_sha256=hashlib.sha256((target/'VC_redist.x64.exe').read_bytes()).hexdigest(), files=manifest),
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('Staged', len(manifest), 'files; bytes=', sum(f.stat().st_size for f in target.rglob('*') if f.is_file()))


if __name__ == '__main__':
    main()
