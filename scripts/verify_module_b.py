"""Explicit D-38 authorized A refresh and real full-data comparison; never overwrite evidence."""
from pathlib import Path
import csv
from contextlib import closing
from decimal import Decimal
import json
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vcr03.cli import SOURCES,DATABASES  # noqa: E402
from vcr03.ingest import service as ingest  # noqa: E402
from vcr03.matching import service,query  # noqa: E402
from vcr03.storage.database import connect  # noqa: E402


def main():
    target = ROOT/'evidence/tests/module-b/full-verification.json'
    if target.exists():
        raise SystemExit('Existing evidence preserved; choose a reviewed new run rather than overwrite.')
    before = {}
    for scope in ('full','performance'):
        with closing(connect(DATABASES[scope])) as db:
            before[scope] = {'source_rows':db.execute('SELECT COUNT(*) FROM source_line').fetchone()[0],
                             'snapshots':[r[0] for r in db.execute('SELECT snapshot_id FROM analysis_snapshot')],
                             'active':db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0]}
    # This technical refresh is only for D-38 already approved by the user.
    refreshed = {}
    for scope in ('full','performance'):
        print('Approved unchanged A refresh: '+scope,flush=True)
        refreshed[scope] = ingest.ingest_workbook(SOURCES[scope],DATABASES[scope],approved_unchanged_rule_refresh=True)
        print(json.dumps({'scope':scope,'refresh':refreshed[scope]['summary'].get('approved_refresh')},ensure_ascii=False),flush=True)
    start = perf_counter()
    full = service.reconcile_workbook(SOURCES['full'],DATABASES['full'])
    elapsed = perf_counter()-start
    sample = service.reconcile_workbook(SOURCES['performance'],DATABASES['performance'])
    legacy_file = ROOT/'evidence/data/review-2026-10-14/batch_line_results.csv'
    with legacy_file.open(encoding='utf-8-sig',newline='') as f:
        legacy = {(r['sheet'],int(r['excel_row'])):r for r in csv.DictReader(f)}
    differences,not_computed,source_changes = [],[],{}
    mid = full['matching']['match_id']
    with closing(connect(DATABASES['full'])) as db:
        for row in db.execute('SELECT r.*,s.sheet,s.excel_row FROM match_result r JOIN source_line s ON s.line_id=r.cancel_line_id WHERE match_id=?',(mid,)):
            key = (row['sheet'],row['excel_row'])
            old = legacy.pop(key,None)
            if old is None:
                differences.append({'source':key,'reason':'new result absent from historical dry run'})
                continue
            fields = {}
            for field in ('classification','primary_reason'):
                current = row[field] or 'MATCHED_AUTO'
                if current != old[field]:
                    fields[field] = {'old':old[field],'current':current}
            if row['candidate_count'] is None:
                not_computed.append({'source':key,'old_count':int(old['candidate_count']), 'current_count':None,
                                     'reason':row['primary_reason']})
            elif row['candidate_count'] != int(old['candidate_count']):
                fields['candidate_count'] = {'old':old['candidate_count'],'current':row['candidate_count']}
            original = json.loads(old['original']) if old['original'] else None
            current_original = (tuple(db.execute('SELECT sheet,excel_row FROM source_line WHERE line_id=?',(row['original_line_id'],)).fetchone())
                                if row['original_line_id'] is not None else None)
            expected = (original['sheet'],original['excel_row']) if original else None
            if current_original != expected:
                fields['original'] = {'old':expected,'current':current_original}
            old_delta = Decimal(old['price_delta']) if old['price_delta'] not in ('','None') else None
            new_delta = Decimal(row['price_delta']) if row['price_delta'] is not None else None
            if old_delta != new_delta:
                fields['price_delta'] = {'old':str(old_delta),'current':str(new_delta)}
            if fields:
                differences.append({'source':key,'fields':fields})
        for key in legacy:
            differences.append({'source':key,'reason':'historical result absent from current results'})
        snapshot = full['import']['snapshot_id']
        columns = [r['name'] for r in db.execute('PRAGMA table_info(line_analysis)') if r['name'] not in {'snapshot_id'}]
        condition = ' OR '.join(f'n.{col} IS NOT o.{col}' for col in columns)
        for old in before['full']['snapshots']:
            source_changes[old] = db.execute(f'SELECT COUNT(*) FROM line_analysis n JOIN line_analysis o USING(line_id) WHERE n.snapshot_id=? AND o.snapshot_id=? AND ({condition})',(snapshot,old)).fetchone()[0]
        overallocated = db.execute('''SELECT COUNT(*) FROM (SELECT l.original_line_id,SUM(l.delta) used,a.quantity
            FROM ledger_entry l JOIN line_analysis a ON a.line_id=l.original_line_id AND a.snapshot_id=?
            GROUP BY l.original_line_id HAVING used>a.quantity OR used<0)''',(snapshot,)).fetchone()[0]
        actual = {table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
                  ('source_line','analysis_snapshot','match_result','candidate','ticket','ticket_line','processing_round','allocation','ledger_entry')}
        fk = [tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
    report = {'verified_at':ingest.now(),'scope':'Both official sheets; active D-38 A/B; source and historical evidence retained',
              'before':before,'refresh':refreshed,'full':full,'sample':sample,'full_pipeline_wall_seconds':elapsed,
              'compared_commodity_rows':full['matching']['summary']['commodity_cancellations'],
              'business_differences':differences,'historical_zero_to_not_computed':not_computed,
              'evidence_semantics':'DP-10 requires uncomputed candidate counts to be null; historical dry run used zero for missing customer.',
              'old_A_output_changes':source_changes,'actual_table_counts':actual,'overallocated_originals':overallocated,
              'foreign_key_errors':fk,'source_sha256_after':ingest.file_hash(SOURCES['full']),
              'persisted_query_match_id':query.overview(DATABASES['full'])['match_id']}
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'business_differences':len(differences),'historical_zero_to_not_computed':len(not_computed),
                      'old_A_output_changes':source_changes,'counts':actual,'summary':full['matching']['summary']},ensure_ascii=False,indent=2),flush=True)
    if differences or any(source_changes.values()) or overallocated or fk:
        raise SystemExit('Verification difference recorded; stop and report before proceeding.')


if __name__=='__main__':
    main()
