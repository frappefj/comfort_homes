"""Local static/syntax/package checks; does not install or contact a site."""
from pathlib import Path
import ast
import json
import subprocess
import sys
import tempfile
import zipfile
from jinja2 import Environment

ROOT=Path(__file__).resolve().parents[1]

def check():
    result={"python_sources":0,"json_files":0,"javascript_sources":0,"server_scripts":0,"jinja_sources":0}
    sources=[];templates=[]
    for p in (ROOT/'comfort_homes').rglob('*'):
        if not p.is_file() or '__pycache__' in p.parts:continue
        if p.suffix=='.py':ast.parse(p.read_text(),filename=str(p));result['python_sources']+=1
        if p.suffix=='.json':json.loads(p.read_text());result['json_files']+=1
        if p.suffix=='.js':sources.append((p.name,p.read_text()))
        if p.suffix=='.html' and 'tests' not in p.parts:templates.append(p.read_text())
    for p in (ROOT/'comfort_homes/fixtures').glob('*.json'):
        for r in json.loads(p.read_text()):
            if r['doctype']=='Server Script':ast.parse(r.get('script') or 'pass');result['server_scripts']+=1
            if r['doctype']=='Client Script':sources.append((r['name'],r.get('script') or ''))
            if r['doctype']=='Web Form':sources.append((r['name'],r.get('client_script') or ''))
            if r['doctype']=='Print Format' and r.get('html'):templates.append(r['html'])
    with tempfile.TemporaryDirectory() as temp:
        for i,(label,source) in enumerate(sources):
            p=Path(temp)/f'check-{i}.js';p.write_text(source)
            completed=subprocess.run(['node','--check',str(p)],capture_output=True,text=True)
            if completed.returncode:raise RuntimeError(label+': '+completed.stderr)
            result['javascript_sources']+=1
    for html in templates:Environment().parse(html);result['jinja_sources']+=1
    return result


def check_wheel(path):
    with zipfile.ZipFile(path) as archive:
        names=set(archive.namelist());checked=0
        for p in (ROOT/'comfort_homes').rglob('*'):
            if not p.is_file() or '__pycache__' in p.parts or p.name.startswith('.'):continue
            if p.suffix not in {'.py','.js','.json','.html','.csv','.txt'}:continue
            key=p.relative_to(ROOT).as_posix()
            if key not in names or archive.read(key)!=p.read_bytes():raise AssertionError('Wheel missing/different: '+key)
            checked+=1
    return dict(wheel_runtime_sources_checked=checked,wheel_complete=True)

if __name__=='__main__':
    result=check()
    if len(sys.argv)>1:result.update(check_wheel(sys.argv[1]))
    print(json.dumps(result,indent=2))
