"""A -> complete B analysis -> atomic initial allocation and ticket publication."""
from collections import Counter
from contextlib import closing
import json
import sqlite3
from time import perf_counter

from vcr03.ingest import service as ingest
from vcr03.ingest.rules import compact, digest
from vcr03.matching.rules import MATCH_VERSION, RULES, analyze, summarize_invoices
from vcr03.matching.storage import initialize
from vcr03.storage.database import connect
from vcr03.workflow.allocation import allocate_in_transaction


class ReconciliationRejected(ValueError):
    pass


def load_rows(db, snapshot_id):
    columns = '''line_id,invoice_key,stock_key,customer_key,quantity,price,invoice_date,
                 line_kind,primary_reason,quality_tags,candidate_comparable'''
    def rows(condition):
        values = []
        for stored in db.execute(f'SELECT {columns} FROM line_analysis WHERE snapshot_id=? AND disposition=? AND '+condition,
                                 (snapshot_id, 'RETAINED')):
            row = dict(stored)
            row['quality'] = json.loads(row.pop('quality_tags'))
            values.append(row)
        return values
    return rows("side='SALE' AND candidate_comparable=1"), rows("side='CANCEL'")


def make_summary(a, results, candidates, demands, invoices):
    classes = Counter(r['classification'] for r in results)
    invoice_classes = Counter(i['classification'] for i in invoices)
    anomalies = [i for i in invoices if i['classification'] in {'SUSPECTED', 'NO_ORIGINAL'}]
    automatic = [i for i in invoices if i['classification'] == 'MATCHED_AUTO']
    pure = [i for i in invoices if i['classification'] == 'NONPRODUCT_ONLY']
    goods = len(results)
    nonproduct = sum(len(i['nonproduct_line_ids']) for i in invoices)
    detail = sum(len(i['commodity_line_ids']) for i in anomalies)
    auto_detail = sum(len(i['commodity_line_ids']) for i in automatic)
    checks = [
        ('file_rows', a['raw_rows'], a['retained_rows']+a['excluded_rows']),
        ('retained_sides', a['retained_rows'], sum(a['side_counts'].values())),
        ('cancellation_rows', a['side_counts'].get('CANCEL', 0), goods+nonproduct),
        ('commodity_classes', goods, sum(classes.values())),
        ('invoice_scope', len(invoices), len(invoices)-len(pure)+len(pure)),
        ('commodity_invoice_classes', len(invoices)-len(pure), sum(invoice_classes[c] for c in ('MATCHED_AUTO','SUSPECTED','NO_ORIGINAL'))),
        ('invoice_partition', len(invoices), len(anomalies)+len(automatic)+len(pure)),
        ('commodity_details', goods, detail+auto_detail),
        ('nonproduct_sources', nonproduct, sum(len(i['nonproduct_line_ids']) for group in (anomalies, automatic, pure) for i in group)),
        ('exclusive_reasons', goods, classes['MATCHED_AUTO']+sum(r['primary_reason'] is not None for r in results)),
    ]
    balances = [{'name': name, 'left': left, 'right': right, 'valid': left == right} for name,left,right in checks]
    if not all(c['valid'] for c in balances):
        raise ReconciliationRejected('CLASSIFICATION_BALANCE_FAILED')
    return {'scope': 'initial matching; counts are source lines or invoices, not review conclusions',
            'row_classes': dict(classes), 'invoice_classes': dict(invoice_classes),
            'primary_reasons': dict(Counter(r['primary_reason'] or 'MATCHED_AUTO' for r in results)),
            'reason_tags_nonexclusive': dict(Counter(t['code'] for r in results for t in r['reason_tags'])),
            'commodity_cancellations': goods, 'candidate_links': len(candidates),
            'unique_candidate_rows': sum(r['candidate_count'] == 1 for r in results),
            'over_quota_originals': sum(d['over_quota'] for d in demands),
            'ticket_count': len(anomalies), 'ticket_types': dict(Counter(i['ticket_type'] for i in anomalies)),
            'ticket_commodity_lines': detail, 'automatic_invoice_commodity_lines': auto_detail,
            'source_quality_rows': a['quality_rows'],
            'cancellations_affected_by_candidate_quality': sum(r['candidate_quality'] for r in results),
            'own_cancellation_quality_rows': sum(r['own_quality'] for r in results),
            'amount_incomplete_invoices': sum(not i['amount_complete'] for i in invoices),
            'balances': balances}


def match_snapshot(database, snapshot_id=None, *, rule_version=MATCH_VERSION):
    initialize(database)
    started = perf_counter()
    with closing(connect(database)) as db:
        active = db.execute('SELECT a.* FROM analysis_snapshot a JOIN active_analysis x USING(snapshot_id)').fetchone()
        if not active or active['status'] != 'A_COMPLETE':
            raise ReconciliationRejected('A_SNAPSHOT_REQUIRED')
        snapshot_id = snapshot_id or active['snapshot_id']
        if snapshot_id != active['snapshot_id']:
            raise ReconciliationRejected('STALE_ANALYSIS')
        dataset = active['dataset_id']
        mid = digest([dataset, snapshot_id, rule_version])
        request_key = 'initial:'+mid
        payload = digest([dataset, snapshot_id, rule_version])
        existing = db.execute('SELECT * FROM operation_receipt WHERE request_key=?', (request_key,)).fetchone()
        if existing:
            return {**json.loads(existing['result_json']), 'reused': True}
        sales, cancel_rows = load_rows(db, snapshot_id)
        results, candidates, demands = analyze(sales, [r for r in cancel_rows if r['line_kind'] != 'NONPRODUCT'])
        invoices = summarize_invoices(cancel_rows, results)
        summary = make_summary(json.loads(active['summary_json']), results, candidates, demands, invoices)
        del sales
        try:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT snapshot_id FROM active_analysis WHERE dataset_id=?', (dataset,)).fetchone()
            if not current or current[0] != snapshot_id:
                raise ReconciliationRejected('STALE_ANALYSIS')
            existing = db.execute('SELECT * FROM operation_receipt WHERE request_key=?', (request_key,)).fetchone()
            if existing:
                db.rollback()
                return {**json.loads(existing['result_json']), 'reused': True}
            previous = db.execute('SELECT match_id FROM active_matching WHERE dataset_id=?', (dataset,)).fetchone()
            needs_review = bool(previous) or rule_version != MATCH_VERSION
            producer = 'SYSTEM:'+rule_version
            timestamp = ingest.now()
            db.execute('INSERT INTO matching_snapshot VALUES(?,?,?,?,?,?,?,?)',
                       (mid, dataset, snapshot_id, rule_version, compact({**RULES,'version':rule_version}),
                        'REQUIRES_REVIEW' if needs_review else 'COMMITTING', compact(summary), timestamp))
            db.executemany('INSERT INTO match_result VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           [(mid,r['cancel_line_id'],r['invoice_key'],r['classification'],r['candidate_computed'],
                             r['candidate_count'],r['original_line_id'],r['requested_quantity'],r['price_delta'],
                             r['primary_reason'],compact(r['reason_categories']),compact(r['reason_tags']),
                             r['own_quality'],r['candidate_quality']) for r in results])
            db.executemany('INSERT INTO candidate VALUES(?,?,?,?,?,?)',
                           [(mid,c['cancel_line_id'],c['original_line_id'],c['price_delta'],c['quantity_delta'],compact(c['quality'])) for c in candidates])
            db.executemany('INSERT INTO quota_demand VALUES(?,?,?,?,?,?)',
                           [(mid,d['original_line_id'],d['original_quantity'],d['requested_quantity'],d['claim_count'],d['over_quota']) for d in demands])
            db.executemany('INSERT INTO invoice_result VALUES(?,?,?,?)',
                           [(mid,i['invoice_key'],i['classification'],compact(i)) for i in invoices])
            if not needs_review:
                for r in results:
                    if r['classification'] == 'MATCHED_AUTO':
                        allocate_in_transaction(db, match_id=mid, cancel_line_id=r['cancel_line_id'],
                                                original_line_id=r['original_line_id'], quantity=r['requested_quantity'],
                                                producer=producer, request_key=f'auto:{mid}:{r["cancel_line_id"]}')
                by_id = {r['cancel_line_id']: r for r in results}
                for invoice in invoices:
                    if invoice['classification'] not in {'SUSPECTED','NO_ORIGINAL'}:
                        continue
                    tid = digest(['ticket', dataset, invoice['invoice_key']])
                    db.execute('INSERT INTO ticket VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (tid,dataset,invoice['invoice_key'],invoice['ticket_type'],'PENDING',None,1,1,mid,timestamp))
                    db.execute('INSERT INTO processing_round VALUES(?,?,?,?,?,?)',
                               (tid,1,invoice['ticket_type'],compact(invoice['reasons']),'ticket-type-d38-v1',timestamp))
                    db.executemany('INSERT INTO ticket_line VALUES(?,?,?,?)',
                                   [(tid,lid,mid,int(by_id[lid]['classification'] != 'MATCHED_AUTO')) for lid in invoice['commodity_line_ids']])
                    db.execute('INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?)',
                               (digest(['create',tid]),'CREATE_TICKET',tid,producer,request_key,'{}',
                                compact({'status':'PENDING','ticket_type':invoice['ticket_type'],'round':1}),
                                'Initial anomaly evidence',timestamp))
                db.execute("UPDATE matching_snapshot SET status='ACTIVE' WHERE match_id=?", (mid,))
                db.execute('INSERT INTO active_matching VALUES(?,?)', (dataset,mid))
            status = 'REQUIRES_REVIEW' if needs_review else 'ACTIVE'
            outcome = {'match_id':mid,'dataset_id':dataset,'snapshot_id':snapshot_id,'rule_version':rule_version,
                       'status':status,'reused':False,'summary':summary}
            db.execute('INSERT INTO operation_receipt VALUES(?,?,?,?)', (request_key,payload,compact(outcome),timestamp))
            db.execute('INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?)',
                       (digest(['publish',mid]),'PREVIEW_MATCHING' if needs_review else 'ACTIVATE_MATCHING',mid,producer,
                        request_key,'{}',compact({'status':status}),'Complete snapshot; no temporal priority',timestamp))
            db.commit()
        except sqlite3.OperationalError as exc:
            db.rollback()
            if 'locked' in str(exc).lower():
                raise ReconciliationRejected('DATABASE_BUSY_RETRY_SAME_REQUEST') from exc
            raise
        except Exception:
            db.rollback()
            raise
        # Force a persisted result query before reporting completion/timing.
        count = db.execute('SELECT COUNT(*) FROM match_result WHERE match_id=?', (mid,)).fetchone()[0]
        if count != len(results):
            raise ReconciliationRejected('PERSISTED_COUNT_MISMATCH')
    return {**outcome, 'elapsed_seconds':perf_counter()-started}


def reconcile_workbook(source, database):
    started = perf_counter()
    imported = ingest.ingest_workbook(source, database)
    matching = match_snapshot(database, imported['snapshot_id'])
    return {'import':imported,'matching':matching,'elapsed_seconds':perf_counter()-started}
