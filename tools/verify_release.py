"""Read-only source snapshot verification. Never installs or contacts a site."""
import hashlib
import json
from pathlib import Path
import sys


def verify(root):
    root=Path(root).resolve()
    manifest=json.loads((root/'RELEASE_MANIFEST.json').read_text())
    problems=[]
    for name,digest in manifest['files'].items():
        path=root/name
        if not path.is_relative_to(root):
            problems.append('Unsafe manifest path: '+name)
        elif not path.is_file():
            problems.append('Missing: '+name)
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            problems.append('Different: '+name)
    # Unlisted runtime sources/fixtures can survive a drag-and-drop overlay.
    for path in (root/'comfort_homes').rglob('*'):
        if '__pycache__' in path.parts or not path.is_file():continue
        if path.suffix in {'.py','.json','.js','.html','.csv'}:
            name=path.relative_to(root).as_posix()
            if name not in manifest['files']:problems.append('Unexpected app file: '+name)
    return {'version':manifest['version'],'ok':not problems,'files_checked':len(manifest['files']),
            'problems':problems,'site_tested':False}


if __name__=='__main__':
    result=verify(sys.argv[1] if len(sys.argv)>1 else Path(__file__).resolve().parents[1])
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['ok'] else 1)
