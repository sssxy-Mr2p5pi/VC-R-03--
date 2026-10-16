"""Shared snapshot-scoped query/export layer; current balances are never cached."""
from contextlib import closing
import json

from vcr03.ingest.service import now
from vcr03.storage.database import connect
from vcr03.workflow.allocation import available


REASON_CATEGORIES = ('DATA_QUALITY', 'MATCHING_DISCREPANCY')


def check_reason_category(category):
    """Accept only the empty selection or one fixed reason category."""
    if category not in ('', *REASON_CATEGORIES):
        raise ValueError('INVALID_REASON_CATEGORY')
    return category


def current(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='active_matching'").fetchone():
        return None
    row = db.execute('SELECT m.* FROM matching_snapshot m JOIN active_matching a USING(match_id)').fetchone()
    return dict(row) if row else None


def overview(database):
    if not database.exists():
        return None
    with closing(connect(database)) as db:
        match = current(db)
        if match:
            match['summary'] = json.loads(match.pop('summary_json'))
            match['pending_versions'] = [dict(r) for r in db.execute("SELECT match_id,rule_version,status FROM matching_snapshot WHERE status='REQUIRES_REVIEW'")]
        return match


def evidence(db, snapshot, lid):
    row = db.execute('''SELECT a.*,s.sheet,s.excel_row,s.raw_json,d.source_hash FROM line_analysis a
                       JOIN source_line s USING(line_id) JOIN source_dataset d ON d.dataset_id=s.dataset_id
                       WHERE a.snapshot_id=? AND a.line_id=?''', (snapshot,lid)).fetchone()
    if not row:
        return None
    result = dict(row)
    result['raw'] = json.loads(result.pop('raw_json'))
    result['quality_tags'] = json.loads(result['quality_tags'])
    return result


def results(database, *, page=1, classification='', reason='', category='', limit=50):
    check_reason_category(category)
    with closing(connect(database)) as db:
        match = current(db)
        if not match:
            return {'rows':[], 'total':0, 'page':page, 'pages':1}
        where, args = ' WHERE r.match_id=?', [match['match_id']]
        for field,value in [('classification',classification),('primary_reason',reason)]:
            if value:
                where += f' AND r.{field}=?'
                args.append(value)
        if category:
            where += ' AND EXISTS (SELECT 1 FROM json_each(r.reason_categories) je WHERE je.value=?)'
            args.append(category)
        total = db.execute('SELECT COUNT(*) FROM match_result r'+where,args).fetchone()[0]
        sql = '''SELECT r.*,a.price,a.quantity,a.stock_key,a.customer_key,a.normalization_version,
                 s.sheet,s.excel_row,s.raw_json,l.allocation_id FROM match_result r
                 JOIN matching_snapshot m USING(match_id)
                 JOIN line_analysis a ON a.snapshot_id=m.snapshot_id AND a.line_id=r.cancel_line_id
                 JOIN source_line s ON s.line_id=r.cancel_line_id
                 LEFT JOIN allocation l ON l.dataset_id=m.dataset_id AND l.cancel_line_id=r.cancel_line_id AND l.active=1'''
        sql += where+' ORDER BY r.cancel_line_id LIMIT ? OFFSET ?'
        rows = [dict(r) for r in db.execute(sql, [*args,limit,(page-1)*limit])]
        for row in rows:
            row['raw'] = json.loads(row.pop('raw_json'))
            row['reason_categories'] = json.loads(row['reason_categories'])
        return {'match_id':match['match_id'],'rows':rows,'total':total,'page':page,'pages':max(1,(total+limit-1)//limit)}


def detail(database, lid, *, candidate_page=1, limit=50):
    with closing(connect(database)) as db:
        db.execute('BEGIN')
        match = current(db)
        if not match:
            return None
        row = db.execute('SELECT * FROM match_result WHERE match_id=? AND cancel_line_id=?', (match['match_id'],lid)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['reason_tags'] = json.loads(result['reason_tags'])
        result['reason_categories'] = json.loads(result['reason_categories'])
        result['source'] = evidence(db,match['snapshot_id'],lid)
        result['match'] = match
        result['queried_at'] = now()
        result['allocation'] = [dict(r) for r in db.execute('SELECT * FROM allocation WHERE dataset_id=? AND cancel_line_id=?', (match['dataset_id'],lid))]
        candidates = []
        for row in db.execute('SELECT * FROM candidate WHERE match_id=? AND cancel_line_id=? ORDER BY original_line_id LIMIT ? OFFSET ?',
                              (match['match_id'],lid,limit,(candidate_page-1)*limit)):
            item = dict(row)
            original = evidence(db,match['snapshot_id'],row['original_line_id'])
            item['source'] = original
            item['quality'] = json.loads(item.pop('quality_json'))
            item['available_quantity'] = available(db,original['line_id'],original['quantity'])
            demand = db.execute('SELECT * FROM quota_demand WHERE match_id=? AND original_line_id=?', (match['match_id'],original['line_id'])).fetchone()
            item['demand'] = dict(demand) if demand else None
            item['related'] = [dict(r) for r in db.execute('SELECT cancel_line_id,invoice_key,requested_quantity FROM match_result WHERE match_id=? AND original_line_id=?', (match['match_id'],original['line_id']))]
            candidates.append(item)
        result['candidates'] = candidates
        result['candidate_page'] = candidate_page
        result['candidate_pages'] = max(1,((result['candidate_count'] or 0)+limit-1)//limit)
        return result


def tickets(database, *, page=1, ticket_type='', limit=50):
    with closing(connect(database)) as db:
        match = current(db)
        if not match:
            return {'rows':[],'total':0,'page':page,'pages':1}
        where, args = ' WHERE dataset_id=?', [match['dataset_id']]
        if ticket_type:
            where += ' AND ticket_type=?'
            args.append(ticket_type)
        total = db.execute('SELECT COUNT(*) FROM ticket'+where,args).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT * FROM ticket'+where+' ORDER BY invoice_key LIMIT ? OFFSET ?', [*args,limit,(page-1)*limit])]
        return {'rows':rows,'total':total,'page':page,'pages':max(1,(total+limit-1)//limit)}


def invoice_detail(database, invoice):
    with closing(connect(database)) as db:
        db.execute('BEGIN')
        match = current(db)
        if not match:
            return None
        stored = db.execute('SELECT * FROM invoice_result WHERE match_id=? AND invoice_key=?', (match['match_id'],invoice)).fetchone()
        if not stored:
            return None
        item = json.loads(stored['summary_json'])
        item['match'] = match
        ticket = db.execute('SELECT * FROM ticket WHERE dataset_id=? AND invoice_key=?', (match['dataset_id'],invoice)).fetchone()
        item['ticket'] = dict(ticket) if ticket else None
        item['events'] = []
        item['rounds'] = []
        if ticket:
            item['events'] = [dict(r) for r in db.execute('SELECT * FROM audit_event WHERE object_id=? ORDER BY created_at,event_id',(ticket['ticket_id'],))]
            item['rounds'] = [dict(r) for r in db.execute('SELECT * FROM processing_round WHERE ticket_id=? ORDER BY round_no',(ticket['ticket_id'],))]
        item['lines'] = [dict(r) for r in db.execute('SELECT * FROM match_result WHERE match_id=? AND invoice_key=? ORDER BY cancel_line_id',(match['match_id'],invoice))]
        for row in item['lines']:
            row['source'] = evidence(db,match['snapshot_id'],row['cancel_line_id'])
        item['nonproduct'] = [evidence(db,match['snapshot_id'],lid) for lid in item['nonproduct_line_ids']]
        return item


def export_rows(database, kind, *, classification='', reason='', category='', ticket_type=''):
    """Streaming rows under one read transaction, including original values and versions."""
    check_reason_category(category)
    with closing(connect(database)) as db:
        db.execute('BEGIN')
        match = current(db)
        if not match:
            return
        timestamp = now()
        if kind == 'tickets':
            for r in db.execute('SELECT * FROM ticket WHERE dataset_id=? ORDER BY invoice_key',(match['dataset_id'],)):
                if ticket_type and r['ticket_type'] != ticket_type:
                    continue
                row = dict(r)
                row['rounds'] = [dict(x) for x in db.execute('SELECT * FROM processing_round WHERE ticket_id=?',(r['ticket_id'],))]
                row['history'] = [dict(x) for x in db.execute('SELECT * FROM audit_event WHERE object_id=?',(r['ticket_id'],))]
                yield row
            return
        for r in db.execute('SELECT * FROM match_result WHERE match_id=? ORDER BY cancel_line_id',(match['match_id'],)):
            if (classification and r['classification'] != classification) or (reason and r['primary_reason'] != reason):
                continue
            if category and category not in json.loads(r['reason_categories']):
                continue
            source = evidence(db,match['snapshot_id'],r['cancel_line_id'])
            if kind == 'results':
                row = {**dict(r),'dataset_id':match['dataset_id'],'snapshot_id':match['snapshot_id'],
                       'rule_version':match['rule_version'],'source':source,'queried_at':timestamp}
                row['allocations'] = [dict(a) for a in db.execute('SELECT * FROM allocation WHERE dataset_id=? AND cancel_line_id=?',(match['dataset_id'],r['cancel_line_id']))]
                yield row
            elif kind == 'candidates':
                for c in db.execute('SELECT * FROM candidate WHERE match_id=? AND cancel_line_id=? ORDER BY original_line_id',(match['match_id'],r['cancel_line_id'])):
                    original = evidence(db,match['snapshot_id'],c['original_line_id'])
                    yield {**dict(c),'cancel_source':source,'original_source':original,
                           'available_quantity':available(db,original['line_id'],original['quantity']),
                           'snapshot_id':match['snapshot_id'],'rule_version':match['rule_version'],'queried_at':timestamp}
