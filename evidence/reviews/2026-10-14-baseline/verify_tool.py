"""Isolated baseline-tool checks; these are not course business tests."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--script', type=Path, help='Candidate checker; defaults to the project checker')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    checker = args.script or root / 'scripts/check_baseline.py'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fixtures = Path(tempfile.mkdtemp(prefix='baseline_checks_', dir=args.output.parent))
    manifest = json.loads((root / 'baseline.json').read_text(encoding='utf-8'))
    results = []

    def case(name):
        folder = fixtures / name
        (folder / 'scripts').mkdir(parents=True)
        for filename in [*manifest['files'], 'baseline.json']:
            shutil.copy2(root / filename, folder / filename)
        shutil.copy2(checker, folder / 'scripts/check_baseline.py')
        return folder

    def run(folder, *cli):
        p = subprocess.run([sys.executable, '-X', 'utf8', str(folder / 'scripts/check_baseline.py'), *cli],
                           cwd=folder, capture_output=True, text=True, encoding='utf-8', check=False)
        return {'args': list(cli), 'exit_code': p.returncode, 'output': p.stdout + p.stderr}

    def add_decision(folder):
        path = folder / '决定记录.md'
        text = path.read_text(encoding='utf-8')
        number = max(map(int, re.findall(r'^##\s*D-(\d+)', text, re.M))) + 1
        path.write_text(text + f'\n## D-{number:02d} 隔离工具夹具\n仅用于工具检查，不是业务决定。\n', encoding='utf-8')

    def remove_refs(folder):
        (folder / '怎么算做对.md').write_text('# 隔离工具夹具：故意缺少编号引用\n', encoding='utf-8')

    def record(name, runs, passed, **extra):
        results.append({'name': name, 'passed': passed, 'runs': runs, **extra})

    f = case('healthy')
    r = run(f)
    record('current_baseline_check', [r], r['exit_code'] == 0)

    f = case('hash_drift')
    p = f / '要做什么.md'
    p.write_text(p.read_text(encoding='utf-8') + '\n隔离夹具注记\n', encoding='utf-8')
    r = run(f)
    record('hash_drift_rejected', [r], r['exit_code'] == 1 and '已变更' in r['output'])

    f = case('no_decision')
    before = (f / 'baseline.json').read_bytes()
    r = run(f, '--update', '--note', '隔离夹具：无新决定')
    unchanged = before == (f / 'baseline.json').read_bytes()
    record('update_without_new_decision_rejected', [r], r['exit_code'] == 1 and unchanged,
           manifest_unchanged=unchanged)

    f = case('missing_refs_update')
    add_decision(f)
    remove_refs(f)
    before = (f / 'baseline.json').read_bytes()
    r = run(f, '--update', '--note', '隔离夹具：缺引用')
    unchanged = before == (f / 'baseline.json').read_bytes()
    record('missing_refs_block_update', [r], r['exit_code'] == 1 and unchanged,
           manifest_unchanged=unchanged)

    f = case('missing_refs_force_update')
    remove_refs(f)
    before = (f / 'baseline.json').read_bytes()
    r = run(f, '--update', '--force', '--note', '隔离夹具：force不能跳过引用')
    unchanged = before == (f / 'baseline.json').read_bytes()
    record('force_does_not_skip_refs', [r], r['exit_code'] == 1 and unchanged,
           manifest_unchanged=unchanged)

    f = case('missing_refs_init')
    remove_refs(f)
    (f / 'baseline.json').unlink()  # Exact file inside this explicitly created fixture.
    r = run(f, '--init')
    absent = not (f / 'baseline.json').exists()
    record('invalid_init_does_not_create_manifest', [r], r['exit_code'] == 1 and absent,
           manifest_absent=absent)

    f = case('missing_refs_force_init')
    remove_refs(f)
    before = (f / 'baseline.json').read_bytes()
    r = run(f, '--init', '--force')
    unchanged = before == (f / 'baseline.json').read_bytes()
    record('invalid_reinit_keeps_manifest', [r], r['exit_code'] == 1 and unchanged,
           manifest_unchanged=unchanged)

    f = case('undefined_reference')
    add_decision(f)
    p = f / '怎么算做对.md'
    p.write_text(p.read_text(encoding='utf-8') + '\nDP-999 隔离未定义编号\n', encoding='utf-8')
    before = (f / 'baseline.json').read_bytes()
    r = run(f, '--update', '--note', '隔离夹具：未定义引用')
    unchanged = before == (f / 'baseline.json').read_bytes()
    record('undefined_reference_blocks_update', [r], r['exit_code'] == 1 and unchanged,
           manifest_unchanged=unchanged)

    f = case('valid_update')
    add_decision(f)
    r1 = run(f, '--update', '--note', '隔离夹具：合法更新')
    r2 = run(f)
    updated = json.loads((f / 'baseline.json').read_text(encoding='utf-8'))
    record('valid_update_then_check', [r1, r2], r1['exit_code'] == 0 and r2['exit_code'] == 0
           and len(updated['history']) == len(manifest['history']) + 1)

    f = case('valid_init')
    (f / 'baseline.json').unlink()
    r1 = run(f, '--init')
    r2 = run(f)
    record('valid_init_then_check', [r1, r2], r1['exit_code'] == 0 and r2['exit_code'] == 0)

    report = {'scope': 'baseline tooling only; business test count remains zero',
              'checker_sha256': hashlib.sha256(checker.read_bytes()).hexdigest(),
              'source_baseline': manifest['baseline_version'], 'fixtures': str(fixtures),
              'passed': sum(r['passed'] for r in results), 'total': len(results), 'cases': results}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False, indent=2))
    if report['passed'] != report['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
