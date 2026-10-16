"""C read models for the processing page and metrics; reads never cache balances."""
from contextlib import closing
from datetime import datetime
import json

from vcr03.ingest.service import now
from vcr03.matching.query import evidence
from vcr03.storage.database import connect
from vcr03.workflow.allocation import available


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _json(value):
    return json.loads(value) if value not in (None, '') else None


def ticket_detail(database, ticket_id):
    """Everything the processing page shows for one ticket, scoped per round where needed."""
    if not database.exists():
        return None
    with closing(connect(database)) as db:
        db.execute('BEGIN')
        tables = _tables(db)
        if 'ticket' not in tables:
            return None
        stored = db.execute('SELECT * FROM ticket WHERE ticket_id=?', (ticket_id,)).fetchone()
        if not stored:
            return None
        ticket = dict(stored)
        row = db.execute('SELECT * FROM matching_snapshot WHERE match_id=?', (ticket['initial_match_id'],)).fetchone()
        match = dict(row) if row else None
        snapshot_id = match['snapshot_id'] if match else None

        def line_view(cancel_line_id, status):
            item = {'cancel_line_id': cancel_line_id, 'status': status,
                    'source': evidence(db, snapshot_id, cancel_line_id) if snapshot_id else None}
            result = db.execute('SELECT * FROM match_result WHERE match_id=? AND cancel_line_id=?',
                                (ticket['initial_match_id'], cancel_line_id)).fetchone()
            if result:
                item.update(dict(result))
                item['reason_categories'] = _json(item['reason_categories']) or []
                item['reason_tags'] = _json(item['reason_tags']) or []
            else:
                item.update({'classification': None, 'primary_reason': None, 'requested_quantity': None,
                             'candidate_count': None, 'reason_categories': [], 'reason_tags': []})
            candidates = []
            for candidate in db.execute('SELECT * FROM candidate WHERE match_id=? AND cancel_line_id=? ORDER BY original_line_id',
                                        (ticket['initial_match_id'], cancel_line_id)):
                entry = dict(candidate)
                entry['quality'] = _json(entry.pop('quality_json'))
                entry['source'] = evidence(db, snapshot_id, entry['original_line_id']) if snapshot_id else None
                entry['available_quantity'] = (available(db, entry['original_line_id'], entry['source']['quantity'])
                                               if entry['source'] else None)
                candidates.append(entry)
            item['candidates'] = candidates
            return item

        todo, round_lines, seen = [], [], set()
        todo_rows = []
        if 'processing_todo' in tables:
            todo_rows = [dict(entry) for entry in db.execute('SELECT * FROM processing_todo WHERE ticket_id=? AND round_no=? ORDER BY cancel_line_id',
                                                             (ticket_id, ticket['current_round']))]
        if not todo_rows and ticket['current_round'] == 1 and 'ticket_line' in tables:
            todo_rows = [{'cancel_line_id': entry[0], 'status': 'OPEN'} for entry in db.execute(
                'SELECT cancel_line_id FROM ticket_line WHERE ticket_id=? AND initial_pending=1 ORDER BY cancel_line_id',
                (ticket_id,))]
        for entry in todo_rows:
            seen.add(entry['cancel_line_id'])
            round_lines.append(line_view(entry['cancel_line_id'], entry['status']))
            if entry['status'] == 'OPEN':
                todo.append(line_view(entry['cancel_line_id'], 'OPEN'))
        if 'allocation' in tables and 'ticket_line' in tables:
            for entry in db.execute('''SELECT DISTINCT a.cancel_line_id FROM allocation a
                                       JOIN ticket_line tl ON tl.ticket_id=? AND tl.cancel_line_id=a.cancel_line_id
                                       WHERE a.dataset_id=? AND a.active=1 AND a.producer LIKE 'SYSTEM:%'
                                       ORDER BY a.cancel_line_id''', (ticket_id, ticket['dataset_id'])):
                if entry['cancel_line_id'] not in seen:
                    round_lines.append(line_view(entry['cancel_line_id'], 'AUTO'))
        round_lines.sort(key=lambda item: item['cancel_line_id'])

        todo_ids = {item['cancel_line_id'] for item in todo}
        todo = [item for item in round_lines if item['cancel_line_id'] in todo_ids and item['status'] == 'OPEN']

        conclusions = []
        if 'conclusion_version' in tables:
            for entry in db.execute('SELECT * FROM conclusion_version WHERE ticket_id=? ORDER BY created_at,rowid',
                                    (ticket_id,)):
                item = dict(entry)
                item['submitter_role'] = _json(item.pop('submitter_role_json'))
                conclusions.append(item)
        reviews = []
        if 'review' in tables:
            for entry in db.execute('SELECT * FROM review WHERE ticket_id=? ORDER BY created_at,rowid', (ticket_id,)):
                item = dict(entry)
                item['reviewer_role'] = _json(item.pop('reviewer_role_json'))
                reviews.append(item)
        approvals = []
        if 'approval_request' in tables:
            for entry in db.execute('SELECT * FROM approval_request WHERE ticket_id=? ORDER BY created_at,rowid',
                                    (ticket_id,)):
                item = dict(entry)
                item['escalation'] = _json(item.pop('escalation'))
                approvals.append(item)
        assignments = []
        if 'assignment' in tables:
            for entry in db.execute('SELECT * FROM assignment WHERE ticket_id=? ORDER BY started_at,rowid', (ticket_id,)):
                item = dict(entry)
                item['active'] = bool(item['active'])
                assignments.append(item)
        allocations = []
        if 'allocation' in tables and 'ticket_line' in tables:
            line_ids = [entry[0] for entry in db.execute('SELECT cancel_line_id FROM ticket_line WHERE ticket_id=? ORDER BY cancel_line_id',
                                                         (ticket_id,))]
            if line_ids:
                marks = ','.join('?' * len(line_ids))
                for entry in db.execute(f'SELECT * FROM allocation WHERE dataset_id=? AND cancel_line_id IN ({marks}) '
                                        'ORDER BY created_at,rowid', (ticket['dataset_id'], *line_ids)):
                    item = dict(entry)
                    item['active'] = bool(item['active'])
                    item['ledger'] = [dict(ledger) for ledger in db.execute(
                        'SELECT * FROM ledger_entry WHERE allocation_id=? ORDER BY created_at,rowid', (item['allocation_id'],))]
                    item['reversed'] = any(ledger['reverses_entry_id'] for ledger in item['ledger'])
                    allocations.append(item)
        rounds = []
        if 'processing_round' in tables:
            for entry in db.execute('SELECT * FROM processing_round WHERE ticket_id=? ORDER BY round_no', (ticket_id,)):
                item = dict(entry)
                stored = _json(item.pop('reasons_json')) or []
                item['reasons'] = [reason['code'] if isinstance(reason, dict) else reason for reason in stored]
                rounds.append(item)
        events = []
        if 'audit_event' in tables:
            events = [dict(entry) for entry in db.execute('SELECT * FROM audit_event WHERE object_id=? '
                                                          'ORDER BY created_at,event_id', (ticket_id,))]
        return {'ticket': ticket, 'match': match, 'rounds': rounds, 'todo': todo, 'round_lines': round_lines,
                'conclusions': conclusions, 'reviews': reviews, 'approvals': approvals, 'allocations': allocations,
                'assignments': assignments, 'events': events,
                'last_assignee': assignments[-1]['actor_id'] if assignments else None, 'queried_at': now()}


def metrics(database):
    result = {'reversed_auto_allocations': 0, 'reviews_pass': 0, 'reviews_fail': 0,
              'unreviewed_submitted': 0, 'oldest_wait_seconds': 0, 'average_review_wait_seconds': 0}
    if not database.exists():
        return result
    with closing(connect(database)) as db:
        tables = _tables(db)
        if 'review' not in tables:
            return result
        result['reviews_pass'] = db.execute("SELECT COUNT(*) FROM review WHERE verdict='PASS'").fetchone()[0]
        result['reviews_fail'] = db.execute("SELECT COUNT(*) FROM review WHERE verdict='FAIL'").fetchone()[0]
        result['reversed_auto_allocations'] = db.execute(
            '''SELECT COUNT(DISTINCT a.allocation_id) FROM allocation a
               JOIN ledger_entry le ON le.allocation_id=a.allocation_id
               WHERE a.producer LIKE 'SYSTEM:%' AND a.active=0 AND le.reverses_entry_id IS NOT NULL''').fetchone()[0]
        pending = db.execute('''SELECT c.submitted_at FROM conclusion_version c WHERE c.status='SUBMITTED'
                                AND NOT EXISTS (SELECT 1 FROM review r WHERE r.conclusion_id=c.conclusion_id)''').fetchall()
        pending += db.execute('''SELECT a.created_at FROM allocation a WHERE a.active=1 AND a.producer LIKE 'SYSTEM:%'
                                 AND NOT EXISTS (SELECT 1 FROM review r WHERE r.allocation_id=a.allocation_id)''').fetchall()
        result['unreviewed_submitted'] = len(pending)
        moment = datetime.fromisoformat(now())
        waits = [max(0, (moment - datetime.fromisoformat(row[0])).total_seconds()) for row in pending]
        result['oldest_wait_seconds'] = int(max(waits)) if waits else 0
        spans = db.execute('''SELECT c.submitted_at, r.created_at FROM review r
                              JOIN conclusion_version c ON c.conclusion_id=r.conclusion_id
                              WHERE c.submitted_at IS NOT NULL''').fetchall()
        if spans:
            waits = [(datetime.fromisoformat(row['created_at']) - datetime.fromisoformat(row['submitted_at'])).total_seconds()
                     for row in spans]
            result['average_review_wait_seconds'] = int(sum(waits) / len(waits))
        return result
