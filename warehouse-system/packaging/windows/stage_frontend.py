"""Reconstruct the approved desktop-only frontend without unfinished mobile changes."""
import argparse, hashlib, io, json, re, subprocess, tarfile
from pathlib import Path
APP = Path(__file__).resolve().parents[2]
GIT = ['git', '-c', 'safe.directory='+APP.parent.as_posix(), '-c', 'core.fsmonitor=false', '-C', str(APP.parent)]
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    root=args.output.resolve()
    if not root.is_relative_to(APP/'.runtime') or root.exists():
        raise RuntimeError('Use a new isolated frontend source directory')
    profile=json.loads((Path(__file__).parent/'profiles/desktop-issue-total-v13.json').read_text(encoding='utf-8'))
    source=subprocess.check_output(GIT+['archive','--format=tar',profile['base_commit'],'warehouse-system/frontend'])
    root.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(source),mode='r:') as archive:
        for member in archive:
            if not member.isfile():
                if member.isdir(): continue
                raise RuntimeError('Unexpected archive entry')
            target=(root/member.name).resolve()
            if not target.is_relative_to(root): raise RuntimeError('Archive path escape')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    patch=subprocess.check_output(GIT+['diff','--binary',profile['patch_commit']+'^',profile['patch_commit'],'--','warehouse-system/frontend'])
    changes=subprocess.check_output(GIT+['diff','--name-only',profile['patch_commit']+'^',profile['patch_commit'],'--','warehouse-system/frontend']).decode().splitlines()
    if not changes or any(not x.startswith('warehouse-system/frontend/') for x in changes):
        raise RuntimeError('Unexpected frontend patch scope')
    subprocess.run(GIT+['apply','--whitespace=nowarn','--directory='+root.relative_to(APP.parent).as_posix(),'-'],input=patch,check=True)
    frontend=root/'warehouse-system/frontend'
    for overlay in profile.get('overlays', []):
        for name in overlay['paths']:
            target = (frontend/name).resolve()
            if not target.is_relative_to(frontend) or name not in profile['files']:
                raise RuntimeError('Unexpected overlay path')
            target.write_bytes(subprocess.check_output(GIT+['show', overlay['commit']+':warehouse-system/frontend/'+name]))
    for name in ('src/components/AccountBar.vue', 'src/views/Catalog.vue'):
        target = frontend/name
        target.write_bytes(target.read_bytes().replace(b'\r\n', b'\n'))
    patch = (Path(__file__).parent/'profiles'/profile['packaging_patch']).read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(patch).hexdigest() != profile['packaging_patch_sha256']:
        raise RuntimeError('Packaging patch SHA mismatch')
    subprocess.run(GIT+['-c','core.autocrlf=false','apply','--whitespace=nowarn','--directory='+root.relative_to(APP.parent).as_posix(),'-'],input=patch,check=True)
    actual={f.relative_to(frontend).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in frontend.rglob('*') if f.is_file()}
    if actual != profile['files']: raise RuntimeError('Approved desktop source SHA mismatch')
    if (frontend/'src/mobile').exists() or re.search(r'''["']/m(?:/|["'])''', (frontend/'src/router.js').read_text(encoding='utf-8')):
        raise RuntimeError('Unfinished mobile code in desktop package')
    (root/'source-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Approved desktop source verified:',len(actual),'files')
if __name__=='__main__': main()
