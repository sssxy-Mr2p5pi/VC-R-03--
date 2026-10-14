"""Record five fresh-process A-only timings; this cannot pass NFR-01."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import importlib.metadata
import hashlib
import json
import platform
import sqlite3
import statistics
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    manifest=json.loads((ROOT/'evidence/performance/sample-v1/manifest.json').read_text(encoding='utf-8'))
    folder=ROOT/'evidence/performance/module-a-phase'
    folder.mkdir(parents=True,exist_ok=False)
    runs=[]
    for i in range(1,6):
        database=folder/f'run-{i}.db'
        command=[sys.executable,'-X','utf8','scripts/import_data.py','--source','performance','--database',str(database)]
        p=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=False)
        (folder/f'run-{i}.stdout.txt').write_text(p.stdout,encoding='utf-8')
        (folder/f'run-{i}.stderr.txt').write_text(p.stderr,encoding='utf-8')
        if p.returncode==0:
            result=json.loads(p.stdout)
            assert not result['reused'] and result['summary']['source_sha256']==manifest['sample_sha256']
            assert result['summary']['raw_rows']==10000 and result['summary']['r1_valid']
            runs.append({'run':i,'exit_code':0,'elapsed_seconds':result['elapsed_seconds'],
                         'row_counts':{key:result['summary'][key] for key in ['raw_rows','retained_rows','excluded_rows','nonproduct_rows','quality_rows']},
                         'dataset_id':result['dataset_id'],'snapshot_id':result['snapshot_id']})
        else:
            runs.append({'run':i,'exit_code':p.returncode,'error':p.stderr})
        print(json.dumps(runs[-1],ensure_ascii=False),flush=True)
    values=[r['elapsed_seconds'] for r in runs if r['exit_code']==0]
    hardware={}
    if sys.platform=='win32':
        probe=subprocess.run(['powershell','-NoProfile','-Command',
                              "@{cpu=(Get-CimInstance Win32_Processor).Name;ram_bytes=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory}|ConvertTo-Json -Compress"],
                             capture_output=True,text=True,encoding='utf-8',check=False)
        if probe.returncode==0:
            hardware=json.loads(probe.stdout)
        else:
            hardware={'status':'not obtained','error':probe.stderr}
    files=[p for folder in ['src','config'] for p in (ROOT/folder).rglob('*')
           if p.is_file() and p.suffix in {'.py','.html','.json'}]
    files += [ROOT/'scripts/measure_module_a.py',ROOT/'scripts/import_data.py']
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=False).stdout.strip()
    report={'measured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'scope':'Module A only: hash/read/clean/quality/store/commit/query. B/C absent; NFR-01 not executed.',
            'nfr01_status':'NOT_EXECUTED','sample_manifest':manifest,
            'python':platform.python_version(),'os':platform.platform(),'processor':platform.processor(),
            'sqlite':sqlite3.sqlite_version,'packages':{name:importlib.metadata.version(name) for name in ['Flask','openpyxl','pytest']},
            'hardware':hardware,'git_head':head,'working_tree':'measured before task commit; exact code hashes below',
            'code_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
            'baseline':json.loads((ROOT/'baseline.json').read_text(encoding='utf-8'))['baseline_version'],
            'cache':'Normal OS cache, no cache flush; fresh process and empty database each run',
            'runs':runs,'successful_runs':len(values),'max_seconds':max(values) if values else None,
            'median_seconds':statistics.median(values) if values else None}
    (folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'scope':report['scope'],'successful_runs':len(values),'max_seconds':report['max_seconds'],
                      'median_seconds':report['median_seconds'],'nfr01_status':'NOT_EXECUTED'},ensure_ascii=False))
    if len(values)!=5:
        raise SystemExit(1)


if __name__=='__main__':
    main()
