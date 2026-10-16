"""NFR-01: five fresh processes/empty databases, all outcomes retained."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True,exist_ok=False)
    manifest = json.loads((ROOT/'evidence/performance/sample-v1/manifest.json').read_text(encoding='utf-8'))
    sample = ROOT/manifest['sample']
    if hashlib.sha256(sample.read_bytes()).hexdigest()!=manifest['sample_sha256']:
        raise SystemExit('Fixed sample hash mismatch')
    runs = []
    for i in range(1,6):
        database = folder/f'run-{i}.db'
        proc = subprocess.run([sys.executable,'-X','utf8','scripts/reconcile_data.py','--source','performance','--database',str(database)],
                              cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=False)
        (folder/f'run-{i}.stdout.txt').write_text(proc.stdout,encoding='utf-8')
        (folder/f'run-{i}.stderr.txt').write_text(proc.stderr,encoding='utf-8')
        entry = {'run':i,'exit_code':proc.returncode}
        if proc.returncode==0:
            value = json.loads(proc.stdout)
            a,b = value['import'],value['matching']
            entry.update(elapsed_seconds=value['elapsed_seconds'],a_seconds=a['elapsed_seconds'],b_seconds=b['elapsed_seconds'],
                         matching_summary=b['summary'],source_sha256=a['summary']['source_sha256'],
                         raw_rows=a['summary']['raw_rows'],reused=a['reused'] or b['reused'],status=b['status'])
            entry['output_valid'] = (entry['raw_rows']==10000 and entry['source_sha256']==manifest['sample_sha256']
                                      and not entry['reused'] and b['status']=='ACTIVE' and a['summary']['r1_valid']
                                      and all(x['valid'] for x in b['summary']['balances']))
        runs.append(entry)
        print(json.dumps(entry,ensure_ascii=False),flush=True)
    times = [r['elapsed_seconds'] for r in runs if r['exit_code']==0]
    same = len(times)==5 and all(r['matching_summary']==runs[0]['matching_summary'] for r in runs)
    passed = same and all(r.get('output_valid') for r in runs) and max(times)<=5
    files = [p for directory in ('src','config','tests') for p in (ROOT/directory).rglob('*')
             if p.is_file() and p.suffix in {'.py','.html','.json'}]
    files += [ROOT/'scripts/reconcile_data.py',ROOT/'scripts/measure_pipeline.py']
    hardware = subprocess.run(['powershell','-NoProfile','-Command',
                              '@{cpu=(Get-CimInstance Win32_Processor).Name;ram_bytes=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory}|ConvertTo-Json -Compress'],
                             capture_output=True,text=True,encoding='utf-8',check=False)
    report = {'measured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
              'scope':'NFR-01 fixed sample: open/hash -> A -> B -> allocations/initial tickets/commit -> persisted query',
              'cache':'Normal OS cache; fresh process and empty database each run',
              'sample_manifest':manifest,'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,
              'os':platform.platform(),'hardware':json.loads(hardware.stdout) if hardware.returncode==0 else {'error':hardware.stderr},
              'packages':{n:importlib.metadata.version(n) for n in ('Flask','openpyxl','pytest')},
              'baseline':json.loads((ROOT/'baseline.json').read_text(encoding='utf-8'))['baseline_version'],
              'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'working_tree':'Measured before delivery commit; exact code hashes preserved',
              'code_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
              'runs':runs,'same_outputs':same,'max_seconds':max(times) if times else None,
              'median_seconds':statistics.median(times) if times else None,'passed':passed}
    (folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('max_seconds','median_seconds','same_outputs','passed')},ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__=='__main__':
    main()
