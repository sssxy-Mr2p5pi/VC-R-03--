"""C manual workflow: one serialized write transaction per request, receipts for replay.

State transitions follow the frozen spec: PENDING/IN_PROGRESS/CLOSED with per-round todos,
review FAIL or an approved reversal reopens only the invalidated lines, PENDING merges.
"""
import json
import sqlite3
from contextlib import closing

from vcr03.ingest.rules import compact, digest
from vcr03.ingest.service import now
from vcr03.storage.database import connect
from vcr03.workflow import actors as accounts
from vcr03.workflow.allocation import allocate_manual_in_transaction, reverse_in_transaction
from vcr03.workflow.storage import initialize

CONCLUSION_TYPES = ('PAIRING', 'NON_PAIRING')
TYPE_RULE_VERSION = 'ticket-type-d38-v1'
_TYPE_CATEGORIES = {'DATA_QUALITY': {'DATA_QUALITY'}, 'MATCHING_DISCREPANCY': {'MATCHING_DISCREPANCY'},
                    'MIXED': {'DATA_QUALITY', 'MATCHING_DISCREPANCY'}}


class WorkflowRejected(ValueError):
    pass


def _race_barrier():
    """Test hook: patched to line up racing connections before BEGIN IMMEDIATE; a no-op in runs."""


def _config(actors):
    return actors if actors is not None else accounts.load()


def _actor(config, actor_id):
    item = accounts.get_actor(config, actor_id)
    if not item:
        raise WorkflowRejected(f'UNKNOWN_ACTOR: {actor_id}')
    return item


def _write(database, work):
    initialize(database)
    with closing(connect(database)) as db:
        try:
            _race_barrier()
            db.execute('BEGIN IMMEDIATE')
            try:
                outcome = work(db)
                db.commit()
                return outcome
            except Exception:
                db.rollback()
                raise
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                raise WorkflowRejected('DATABASE_BUSY_RETRY_SAME_REQUEST') from exc
            raise


def _replay(db, request_key, payload):
    found = db.execute('SELECT * FROM operation_receipt WHERE request_key=?', (request_key,)).fetchone()
    if found:
        if found['payload_hash'] != payload:
            raise WorkflowRejected('IDEMPOTENCY_KEY_CONFLICT')
        return True, json.loads(found['result_json'])
    return False, None


def _record(db, request_key, payload, outcome, *, action, object_id, actor, before, after, reason, timestamp):
    db.execute('INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?)',
               (digest(['audit', request_key]), action, object_id, actor, request_key,
                compact(before), compact(after), reason, timestamp))
    db.execute('INSERT INTO operation_receipt VALUES(?,?,?,?)', (request_key, payload, compact(outcome), timestamp))
    return outcome


def _ticket(db, ticket_id):
    ticket = db.execute('SELECT * FROM ticket WHERE ticket_id=?', (ticket_id,)).fetchone()
    if not ticket:
        raise WorkflowRejected(f'TICKET_NOT_FOUND: {ticket_id}')
    return ticket


def _ensure_todo(db, ticket):
    round_no = ticket['current_round']
    exists = db.execute('SELECT 1 FROM processing_todo WHERE ticket_id=? AND round_no=? LIMIT 1',
                        (ticket['ticket_id'], round_no)).fetchone()
    if exists or round_no != 1:
        return
    for row in db.execute('SELECT cancel_line_id FROM ticket_line WHERE ticket_id=? AND initial_pending=1',
                          (ticket['ticket_id'],)):
        db.execute('INSERT OR IGNORE INTO processing_todo VALUES(?,?,?,?,?,?)',
                   (ticket['ticket_id'], 1, row['cancel_line_id'], 'OPEN', None, None))


def _resolve_todo(db, ticket, cancel_line_id, conclusion_id, timestamp):
    db.execute('''UPDATE processing_todo SET status='RESOLVED', resolved_at=?, resolved_by_conclusion=?
                  WHERE ticket_id=? AND round_no=? AND cancel_line_id=? AND status='OPEN' ''',
               (timestamp, conclusion_id, ticket['ticket_id'], ticket['current_round'], cancel_line_id))


def _todo_row(db, ticket, cancel_line_id):
    return db.execute('SELECT * FROM processing_todo WHERE ticket_id=? AND round_no=? AND cancel_line_id=?',
                      (ticket['ticket_id'], ticket['current_round'], cancel_line_id)).fetchone()


def _end_assignment(db, ticket, reason, timestamp):
    db.execute('UPDATE assignment SET active=0, ended_at=?, end_reason=? WHERE ticket_id=? AND active=1',
               (timestamp, reason, ticket['ticket_id']))


def _line_categories(db, match_id, cancel_line_id):
    row = db.execute('SELECT reason_categories FROM match_result WHERE match_id=? AND cancel_line_id=?',
                     (match_id, cancel_line_id)).fetchone()
    return set(json.loads(row['reason_categories'])) if row else set()


def _type_of(categories):
    categories = {c for c in categories if c in ('DATA_QUALITY', 'MATCHING_DISCREPANCY')}
    if not categories:
        return None
    if categories == {'DATA_QUALITY'}:
        return 'DATA_QUALITY'
    if categories == {'MATCHING_DISCREPANCY'}:
        return 'MATCHING_DISCREPANCY'
    return 'MIXED'


def _round_reasons(db, match_id, lines, event, base=()):
    reasons = list(base)
    if event not in reasons:
        reasons.append(event)
    for lid in lines:
        row = db.execute('SELECT primary_reason FROM match_result WHERE match_id=? AND cancel_line_id=?',
                         (match_id, lid)).fetchone()
        if row and row['primary_reason'] and row['primary_reason'] not in reasons:
            reasons.append(row['primary_reason'])
    return reasons


def _insert_round(db, ticket, new_round, lines, event, timestamp):
    match_id = ticket['initial_match_id']
    categories = {'MATCHING_DISCREPANCY'}
    for lid in lines:
        categories |= _line_categories(db, match_id, lid)
    ticket_type = _type_of(categories)
    reasons = _round_reasons(db, match_id, lines, event)
    db.execute('INSERT INTO processing_round VALUES(?,?,?,?,?,?)',
               (ticket['ticket_id'], new_round, ticket_type, compact(reasons), TYPE_RULE_VERSION, timestamp))
    db.execute('UPDATE ticket SET ticket_type=? WHERE ticket_id=?', (ticket_type, ticket['ticket_id']))
    for lid in lines:
        db.execute('INSERT INTO processing_todo VALUES(?,?,?,?,?,?)',
                   (ticket['ticket_id'], new_round, lid, 'OPEN', None, None))
    return ticket_type


def _merge_round(db, ticket, lines, event, timestamp):
    """Open round absorbs new/reopened todos and records the type change; no empty new round."""
    round_no = ticket['current_round']
    match_id = ticket['initial_match_id']
    stored = db.execute('SELECT * FROM processing_round WHERE ticket_id=? AND round_no=?',
                        (ticket['ticket_id'], round_no)).fetchone()
    base = json.loads(stored['reasons_json']) if stored else []
    categories = _TYPE_CATEGORIES.get(stored['ticket_type_snapshot'], set()) if stored else set()
    categories |= {'MATCHING_DISCREPANCY'}
    for lid in lines:
        categories |= _line_categories(db, match_id, lid)
    ticket_type = _type_of(categories)
    reasons = _round_reasons(db, match_id, lines, event, base=base)
    db.execute('UPDATE processing_round SET reasons_json=?, ticket_type_snapshot=? WHERE ticket_id=? AND round_no=?',
               (compact(reasons), ticket_type, ticket['ticket_id'], round_no))
    db.execute('UPDATE ticket SET ticket_type=?, version=version+1 WHERE ticket_id=?',
               (ticket_type, ticket['ticket_id']))
    for lid in lines:
        db.execute('''INSERT INTO processing_todo VALUES(?,?,?,?,?,?)
                      ON CONFLICT(ticket_id,round_no,cancel_line_id)
                      DO UPDATE SET status='OPEN', resolved_at=NULL, resolved_by_conclusion=NULL''',
                   (ticket['ticket_id'], round_no, lid, 'OPEN', None, None))
    return ticket_type


def _open_followup(db, ticket, lines, *, event, timestamp):
    """Apply the invalidated/reversed line to the open-round rules of the state machine."""
    lines = sorted({lid for lid in lines})
    if ticket['status'] == 'CLOSED':
        new_round = ticket['current_round'] + 1
        ticket_type = _insert_round(db, ticket, new_round, lines, event, timestamp)
        _end_assignment(db, ticket, 'REOPENED', timestamp)
        db.execute('UPDATE ticket SET status=?,current_round=?,assignee=NULL,version=version+1 WHERE ticket_id=?',
                   ('PENDING', new_round, ticket['ticket_id']))
        return {'ticket_id': ticket['ticket_id'], 'status': 'PENDING', 'round': new_round, 'ticket_type': ticket_type}
    if ticket['status'] == 'IN_PROGRESS':
        carried = [row['cancel_line_id'] for row in db.execute(
            "SELECT cancel_line_id FROM processing_todo WHERE ticket_id=? AND round_no=? AND status='OPEN'",
            (ticket['ticket_id'], ticket['current_round']))]
        lines = sorted(set(lines) | set(carried))
        new_round = ticket['current_round'] + 1
        ticket_type = _insert_round(db, ticket, new_round, lines, event, timestamp)
        _end_assignment(db, ticket, 'REOPENED', timestamp)
        db.execute('UPDATE ticket SET status=?,current_round=?,assignee=NULL,version=version+1 WHERE ticket_id=?',
                   ('PENDING', new_round, ticket['ticket_id']))
        return {'ticket_id': ticket['ticket_id'], 'status': 'PENDING', 'round': new_round, 'ticket_type': ticket_type}
    ticket_type = _merge_round(db, ticket, lines, event, timestamp)
    return {'ticket_id': ticket['ticket_id'], 'status': 'PENDING', 'round': ticket['current_round'],
            'ticket_type': ticket_type}


def _ticket_state(ticket):
    return {'status': ticket['status'], 'assignee': ticket['assignee'],
            'round': ticket['current_round'], 'ticket_type': ticket['ticket_type']}


def dispatch(database, *, ticket_id, assignee, actor, request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['dispatch', ticket_id, assignee, actor])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        _actor(config, actor)
        _actor(config, assignee)
        if not accounts.has_permission(config, actor, 'DISPATCH'):
            raise WorkflowRejected(f'NO_PERMISSION: {actor} 缺少 DISPATCH（派单）权限')
        ticket = _ticket(db, ticket_id)
        if ticket['status'] != 'PENDING':
            raise WorkflowRejected(f'TICKET_ALREADY_ASSIGNED: 拒绝重复有效派单；当前状态 {ticket["status"]}，'
                                   f'当前责任人 {ticket["assignee"] or "无"}')
        if db.execute('SELECT 1 FROM assignment WHERE ticket_id=? AND active=1', (ticket_id,)).fetchone():
            raise WorkflowRejected('TICKET_ALREADY_ASSIGNED: 该工单已有有效归属')
        timestamp = now()
        assignment_id = digest(['assignment', ticket_id, ticket['current_round'], request_key])
        db.execute('INSERT INTO assignment VALUES(?,?,?,?,?,?,?,?)',
                   (assignment_id, ticket_id, assignee, ticket['current_round'], timestamp, None, None, 1))
        db.execute('UPDATE ticket SET status=?,assignee=?,version=version+1 WHERE ticket_id=?',
                   ('IN_PROGRESS', assignee, ticket_id))
        outcome = {'ticket_id': ticket_id, 'status': 'IN_PROGRESS', 'assignee': assignee, 'assignment_id': assignment_id}
        return _record(db, request_key, payload, outcome, action='DISPATCH', object_id=ticket_id, actor=actor,
                       before=_ticket_state(ticket), after=outcome, reason='授权派单', timestamp=timestamp)
    return _write(database, work)


def save_draft(database, *, ticket_id, cancel_line_id, actor, conclusion_type, original_line_id=None, note='',
               request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['draft', ticket_id, cancel_line_id, actor, conclusion_type, original_line_id, note])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        item = _actor(config, actor)
        if conclusion_type not in CONCLUSION_TYPES:
            raise WorkflowRejected(f'INVALID_CONCLUSION_TYPE: {conclusion_type}')
        if original_line_id is not None and not isinstance(original_line_id, int):
            raise WorkflowRejected('INVALID_CONCLUSION_INPUT: original_line_id 必须是整数或留空')
        ticket = _ticket(db, ticket_id)
        if ticket['status'] != 'IN_PROGRESS' or ticket['assignee'] != actor:
            raise WorkflowRejected(f'NOT_ASSIGNEE: 草稿仅当前责任人保存；当前责任人 {ticket["assignee"] or "无"}，'
                                   f'状态 {ticket["status"]}')
        if not db.execute('SELECT 1 FROM ticket_line WHERE ticket_id=? AND cancel_line_id=?',
                          (ticket_id, cancel_line_id)).fetchone():
            raise WorkflowRejected('LINE_NOT_IN_TICKET: 取消行不属于该工单')
        _ensure_todo(db, ticket)
        todo = _todo_row(db, ticket, cancel_line_id)
        if not todo or todo['status'] != 'OPEN':
            raise WorkflowRejected('LINE_NOT_IN_ROUND_TODO: 该行不在本轮待办中或已解决')
        timestamp = now()
        existing = db.execute('''SELECT * FROM conclusion_version WHERE ticket_id=? AND cancel_line_id=?
                                 AND round_no=? AND submitter=? AND status='DRAFT' ''',
                              (ticket_id, cancel_line_id, ticket['current_round'], actor)).fetchone()
        conclusion_id = existing['conclusion_id'] if existing else digest(
            ['conclusion', ticket_id, cancel_line_id, ticket['current_round'], 'draft', request_key])
        if existing:
            db.execute('''UPDATE conclusion_version SET conclusion_type=?,original_line_id=?,note=?,submitter_role_json=?
                          WHERE conclusion_id=?''',
                       (conclusion_type, original_line_id, note, compact(item), conclusion_id))
        else:
            db.execute('INSERT INTO conclusion_version VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (conclusion_id, ticket_id, cancel_line_id, ticket['current_round'], conclusion_type,
                        original_line_id, note, 'DRAFT', actor, compact(item), timestamp, None, None, '', request_key))
        outcome = {'conclusion_id': conclusion_id, 'status': 'DRAFT', 'cancel_line_id': cancel_line_id}
        return _record(db, request_key, payload, outcome, action='SAVE_DRAFT', object_id=ticket_id, actor=actor,
                       before={'todo': 'OPEN'}, after=outcome, reason='草稿（未生效）', timestamp=timestamp)
    return _write(database, work)


def cancel_draft(database, *, ticket_id, cancel_line_id, actor, request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['cancel-draft', ticket_id, cancel_line_id, actor])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        _actor(config, actor)
        _ticket(db, ticket_id)
        draft = db.execute('''SELECT * FROM conclusion_version WHERE ticket_id=? AND cancel_line_id=?
                              AND submitter=? AND status='DRAFT'
                              ORDER BY round_no DESC, rowid DESC LIMIT 1''',
                           (ticket_id, cancel_line_id, actor)).fetchone()
        if not draft:
            raise WorkflowRejected('DRAFT_NOT_FOUND: 没有可取消的本人草稿')
        timestamp = now()
        db.execute('DELETE FROM conclusion_version WHERE conclusion_id=?', (draft['conclusion_id'],))
        outcome = {'conclusion_id': draft['conclusion_id'], 'status': 'CANCELLED', 'cancel_line_id': cancel_line_id}
        return _record(db, request_key, payload, outcome, action='CANCEL_DRAFT', object_id=ticket_id, actor=actor,
                       before={'status': 'DRAFT'}, after=outcome, reason='取消未提交草稿', timestamp=timestamp)
    return _write(database, work)


def submit_conclusion(database, *, ticket_id, cancel_line_id, actor, conclusion_type, original_line_id=None, note='',
                      request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['submit', ticket_id, cancel_line_id, actor, conclusion_type, original_line_id, note.strip()])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        item = _actor(config, actor)
        if conclusion_type not in CONCLUSION_TYPES:
            raise WorkflowRejected(f'INVALID_CONCLUSION_TYPE: {conclusion_type}')
        if not note.strip():
            raise WorkflowRejected('NOTE_REQUIRED: 行结论必须附证据或说明')
        ticket = _ticket(db, ticket_id)
        if ticket['status'] != 'IN_PROGRESS':
            raise WorkflowRejected(f'INVALID_STATE: 仅处理中工单可提交行结论（当前 {ticket["status"]}）')
        if ticket['assignee'] != actor:
            raise WorkflowRejected(f'NOT_ASSIGNEE: 仅当前责任人可提交（当前责任人 {ticket["assignee"] or "无"}）')
        _ensure_todo(db, ticket)
        existing = db.execute('''SELECT * FROM conclusion_version WHERE ticket_id=? AND cancel_line_id=?
                                 AND round_no=? AND status='SUBMITTED' ''',
                              (ticket_id, cancel_line_id, ticket['current_round'])).fetchone()
        if existing:
            raise WorkflowRejected(f'CONCLUSION_EXISTS: 该行本轮已有有效结论 {existing["conclusion_id"]}')
        todo = _todo_row(db, ticket, cancel_line_id)
        if not todo or todo['status'] != 'OPEN':
            raise WorkflowRejected('LINE_NOT_IN_ROUND_TODO: 该行不在本轮待办中或已解决')
        if not db.execute('SELECT 1 FROM ticket_line WHERE ticket_id=? AND cancel_line_id=?',
                          (ticket_id, cancel_line_id)).fetchone():
            raise WorkflowRejected('LINE_NOT_IN_TICKET: 取消行不属于该工单')
        timestamp = now()
        allocation_id = None
        chosen_original = original_line_id
        if conclusion_type == 'PAIRING':
            if chosen_original is None:
                raise WorkflowRejected('ORIGINAL_REQUIRED: 配对结论必须选择一条候选原单行')
            result = db.execute('SELECT * FROM match_result WHERE match_id=? AND cancel_line_id=?',
                                (ticket['initial_match_id'], cancel_line_id)).fetchone()
            quantity = result['requested_quantity'] if result else None
            if not quantity or type(quantity) is not int or quantity <= 0:
                raise WorkflowRejected('REQUESTED_QUANTITY_UNAVAILABLE: 该行无法计算有效数量，不能配对')
            allocation = allocate_manual_in_transaction(
                db, match_id=ticket['initial_match_id'], cancel_line_id=cancel_line_id,
                original_line_id=chosen_original, quantity=quantity,
                producer='ACTOR:' + actor, request_key='manual:' + request_key)
            allocation_id = allocation['allocation_id']
        else:
            chosen_original = None
        draft = db.execute('''SELECT * FROM conclusion_version WHERE ticket_id=? AND cancel_line_id=?
                              AND round_no=? AND submitter=? AND status='DRAFT' ''',
                           (ticket_id, cancel_line_id, ticket['current_round'], actor)).fetchone()
        conclusion_id = draft['conclusion_id'] if draft else digest(
            ['conclusion', ticket_id, cancel_line_id, ticket['current_round'], request_key])
        if draft:
            db.execute('''UPDATE conclusion_version SET conclusion_type=?,original_line_id=?,note=?,status='SUBMITTED',
                          submitter_role_json=?,submitted_at=?,request_key=? WHERE conclusion_id=?''',
                       (conclusion_type, chosen_original, note.strip(), compact(item), timestamp, request_key, conclusion_id))
        else:
            db.execute('INSERT INTO conclusion_version VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (conclusion_id, ticket_id, cancel_line_id, ticket['current_round'], conclusion_type,
                        chosen_original, note.strip(), 'SUBMITTED', actor, compact(item), timestamp,
                        timestamp, None, '', request_key))
        _resolve_todo(db, ticket, cancel_line_id, conclusion_id, timestamp)
        outcome = {'conclusion_id': conclusion_id, 'status': 'SUBMITTED', 'conclusion_type': conclusion_type,
                   'allocation_id': allocation_id, 'cancel_line_id': cancel_line_id}
        return _record(db, request_key, payload, outcome, action='SUBMIT_CONCLUSION', object_id=ticket_id, actor=actor,
                       before={'todo': 'OPEN', 'allocation': None}, after=outcome,
                       reason=note.strip(), timestamp=timestamp)
    return _write(database, work)


def close_ticket(database, *, ticket_id, actor, note, request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['close', ticket_id, actor, note.strip()])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        _actor(config, actor)
        ticket = _ticket(db, ticket_id)
        if ticket['status'] != 'IN_PROGRESS':
            raise WorkflowRejected(f'INVALID_STATE: 仅处理中工单可关闭（当前 {ticket["status"]}）')
        if ticket['assignee'] != actor:
            raise WorkflowRejected(f'NOT_ASSIGNEE: 仅当前责任人可关闭（当前责任人 {ticket["assignee"] or "无"}）')
        if not note.strip():
            raise WorkflowRejected('NOTE_REQUIRED: 关闭必须填写处理说明')
        _ensure_todo(db, ticket)
        open_rows = [row['cancel_line_id'] for row in db.execute(
            "SELECT cancel_line_id FROM processing_todo WHERE ticket_id=? AND round_no=? AND status='OPEN' "
            "ORDER BY cancel_line_id", (ticket_id, ticket['current_round']))]
        if open_rows:
            raise WorkflowRejected(f'OPEN_TODO_REMAINS: 本轮仍有 {len(open_rows)} 行待办未结（'
                                   f'{",".join(str(x) for x in open_rows)}）')
        timestamp = now()
        _end_assignment(db, ticket, 'CLOSED', timestamp)
        db.execute('UPDATE ticket SET status=?,version=version+1 WHERE ticket_id=?', ('CLOSED', ticket_id))
        outcome = {'ticket_id': ticket_id, 'status': 'CLOSED', 'note': note.strip()}
        return _record(db, request_key, payload, outcome, action='CLOSE_TICKET', object_id=ticket_id, actor=actor,
                       before=_ticket_state(ticket), after=outcome, reason=note.strip(), timestamp=timestamp)
    return _write(database, work)


def request_release(database, *, ticket_id, actor, reason='', request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['release-request', ticket_id, actor, reason.strip()])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        _actor(config, actor)
        ticket = _ticket(db, ticket_id)
        if ticket['status'] != 'IN_PROGRESS':
            raise WorkflowRejected(f'INVALID_STATE: 仅处理中工单可申请释放（当前 {ticket["status"]}）')
        duplicate = db.execute("SELECT request_id FROM approval_request WHERE ticket_id=? AND action='RELEASE' "
                               "AND status='OPEN'", (ticket_id,)).fetchone()
        if duplicate:
            raise WorkflowRejected(f'DUPLICATE_REQUEST: 已有待批释放申请 {duplicate["request_id"]}')
        timestamp = now()
        approvers = accounts.qualified(config, 'APPROVE_RELEASE', exclude=[ticket['assignee'] or ''])
        escalation = compact({'rule': '批准人≠当前责任人；责任人是主管时转其上级或另一合格主管',
                              'current_assignee': ticket['assignee'], 'qualified_approvers': approvers})
        request_id = digest(['approval', ticket_id, ticket['current_round'], 'RELEASE', request_key])
        db.execute('INSERT INTO approval_request VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (request_id, 'RELEASE', ticket_id, ticket['current_round'], None, None, actor, reason.strip(),
                    'OPEN', None, '', escalation, None, timestamp, request_key))
        outcome = {'request_id': request_id, 'status': 'OPEN', 'action': 'RELEASE', 'escalation': json.loads(escalation)}
        return _record(db, request_key, payload, outcome, action='REQUEST_RELEASE', object_id=ticket_id, actor=actor,
                       before={'assignee': ticket['assignee']}, after=outcome,
                       reason=reason.strip() or '释放归属申请', timestamp=timestamp)
    return _write(database, work)


def decide_release(database, *, request_id, actor, approve, note='', request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['release-decide', request_id, actor, bool(approve), note.strip()])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        _actor(config, actor)
        if not accounts.has_permission(config, actor, 'APPROVE_RELEASE'):
            raise WorkflowRejected(f'NO_PERMISSION: {actor} 缺少 APPROVE_RELEASE（批准释放）权限')
        request = db.execute('SELECT * FROM approval_request WHERE request_id=?', (request_id,)).fetchone()
        if not request or request['action'] != 'RELEASE':
            raise WorkflowRejected(f'REQUEST_NOT_FOUND: {request_id}')
        if request['status'] != 'OPEN':
            raise WorkflowRejected(f'REQUEST_NOT_OPEN: 该申请已是 {request["status"]}')
        ticket = _ticket(db, request['ticket_id'])
        if ticket['status'] != 'IN_PROGRESS':
            raise WorkflowRejected(f'OBJECT_STATE_CHANGED: 工单当前状态 {ticket["status"]}，不再处理中')
        if ticket['assignee'] == actor:
            raise WorkflowRejected('SELF_APPROVAL_FORBIDDEN: 批准人必须与当前责任人不同（责任人是主管时转其上级或另一合格主管）')
        timestamp = now()
        if approve:
            _end_assignment(db, ticket, 'RELEASED', timestamp)
            db.execute('UPDATE ticket SET status=?,assignee=NULL,version=version+1 WHERE ticket_id=?',
                       ('PENDING', ticket['ticket_id']))
        db.execute('UPDATE approval_request SET status=?,approver=?,decision_note=?,decided_at=? WHERE request_id=?',
                   ('APPROVED' if approve else 'REJECTED', actor, note.strip(), timestamp, request_id))
        outcome = {'request_id': request_id, 'action': 'RELEASE', 'status': 'APPROVED' if approve else 'REJECTED',
                   'ticket_id': ticket['ticket_id'], 'assignee': None if approve else ticket['assignee']}
        return _record(db, request_key, payload, outcome, action='DECIDE_RELEASE', object_id=ticket['ticket_id'],
                       actor=actor, before=_ticket_state(ticket), after=outcome,
                       reason=note.strip() or ('批准释放' if approve else '不批准释放'), timestamp=timestamp)
    return _write(database, work)


def review_line(database, *, ticket_id, cancel_line_id, actor, verdict, reason='', request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['review', ticket_id, cancel_line_id, actor, verdict, reason.strip()])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        item = _actor(config, actor)
        if not accounts.has_permission(config, actor, 'REVIEW'):
            raise WorkflowRejected(f'NO_PERMISSION: {actor} 缺少 REVIEW（逐行复核）权限')
        if verdict not in ('PASS', 'FAIL'):
            raise WorkflowRejected(f'INVALID_VERDICT: {verdict}')
        if verdict == 'FAIL' and not reason.strip():
            raise WorkflowRejected('REASON_REQUIRED: 否决不通过必须填写理由')
        ticket = _ticket(db, ticket_id)
        if not db.execute('SELECT 1 FROM ticket_line WHERE ticket_id=? AND cancel_line_id=?',
                          (ticket_id, cancel_line_id)).fetchone():
            raise WorkflowRejected('LINE_NOT_IN_TICKET: 取消行不属于该工单')
        _ensure_todo(db, ticket)
        timestamp = now()
        conclusion = db.execute('''SELECT * FROM conclusion_version WHERE ticket_id=? AND cancel_line_id=?
                                   AND round_no=? AND status='SUBMITTED'
                                   ORDER BY created_at DESC, rowid DESC LIMIT 1''',
                                (ticket_id, cancel_line_id, ticket['current_round'])).fetchone()
        allocation = db.execute('SELECT * FROM allocation WHERE dataset_id=? AND cancel_line_id=? AND active=1',
                                (ticket['dataset_id'], cancel_line_id)).fetchone()
        if conclusion:
            target_kind = 'CONCLUSION'
            conclusion_id, allocation_id = conclusion['conclusion_id'], None
            if conclusion['submitter'] == actor:
                raise WorkflowRejected('SELF_REVIEW_FORBIDDEN: 复核人不能是被复核版本的提交人')
            submitter_role = json.loads(conclusion['submitter_role_json'])['business_role']
            if submitter_role == item['business_role'] and actor not in set(accounts.chain_up(config, conclusion['submitter'])):
                raise WorkflowRejected('SAME_ROLE_REVIEW_FORBIDDEN: 同业务角色仅已配置管理链上的上级可复核')
            submitter_snapshot = compact({'id': conclusion['submitter'], 'business_role': submitter_role})
        elif allocation and str(allocation['producer']).startswith('SYSTEM:'):
            target_kind = 'AUTO_ALLOCATION'
            conclusion_id, allocation_id = None, allocation['allocation_id']
            submitter_snapshot = 'SYSTEM'
        else:
            raise WorkflowRejected('NOTHING_TO_REVIEW: 该行当前回合没有可复核的已提交结论或自动配对')
        review_id = digest(['review', ticket_id, cancel_line_id, conclusion_id or allocation_id, request_key])
        db.execute('INSERT INTO review VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (review_id, ticket_id, cancel_line_id, ticket['current_round'], target_kind,
                    conclusion_id, allocation_id, verdict, reason.strip(), actor, compact(item),
                    submitter_snapshot, timestamp, request_key))
        transition = None
        if verdict == 'FAIL':
            if target_kind == 'CONCLUSION':
                db.execute("UPDATE conclusion_version SET status='INVALIDATED',invalidated_at=?,invalid_reason=? "
                           "WHERE conclusion_id=?", (timestamp, reason.strip(), conclusion_id))
                if conclusion['conclusion_type'] == 'PAIRING' and allocation:
                    reverse_in_transaction(db, allocation_id=allocation['allocation_id'],
                                           producer='REVIEW:' + actor, request_key='review:' + review_id,
                                           reason=reason.strip())
            else:
                reverse_in_transaction(db, allocation_id=allocation['allocation_id'],
                                       producer='REVIEW:' + actor, request_key='review:' + review_id,
                                       reason=reason.strip())
            transition = _open_followup(db, ticket, [cancel_line_id], event='REVIEW_FAILED', timestamp=timestamp)
            transition['before'] = _ticket_state(ticket)
        outcome = {'review_id': review_id, 'verdict': verdict, 'target_kind': target_kind,
                   'ticket_id': ticket_id, 'cancel_line_id': cancel_line_id, 'transition': transition}
        return _record(db, request_key, payload, outcome, action='REVIEW_LINE', object_id=ticket_id, actor=actor,
                       before={'target_kind': target_kind, 'verdict': None}, after=outcome,
                       reason=reason.strip() or '复核通过', timestamp=timestamp)
    return _write(database, work)


def request_reversal(database, *, ticket_id, cancel_line_id, actor, reason, request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['reversal-request', ticket_id, cancel_line_id, actor, reason.strip()])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        _actor(config, actor)
        if not reason.strip():
            raise WorkflowRejected('REASON_REQUIRED: 冲销申请必须填写理由')
        ticket = _ticket(db, ticket_id)
        if not db.execute('SELECT 1 FROM ticket_line WHERE ticket_id=? AND cancel_line_id=?',
                          (ticket_id, cancel_line_id)).fetchone():
            raise WorkflowRejected('LINE_NOT_IN_TICKET: 取消行不属于该工单')
        allocation = db.execute('SELECT * FROM allocation WHERE dataset_id=? AND cancel_line_id=? AND active=1',
                                (ticket['dataset_id'], cancel_line_id)).fetchone()
        if not allocation:
            raise WorkflowRejected('NO_ACTIVE_ALLOCATION: 该行没有可冲销的有效配对')
        duplicate = db.execute("SELECT request_id FROM approval_request WHERE allocation_id=? AND action='REVERSAL' "
                               "AND status='OPEN'", (allocation['allocation_id'],)).fetchone()
        if duplicate:
            raise WorkflowRejected(f'DUPLICATE_REQUEST: 该配对已有待批冲销申请 {duplicate["request_id"]}')
        producer_actor = allocation['producer'].split(':', 1)[1] if str(allocation['producer']).startswith('ACTOR:') else ''
        approvers = accounts.qualified(config, 'APPROVE_REVERSAL', exclude=[producer_actor] if producer_actor else [])
        escalation = compact({'rule': '批准人须获授权且≠配对产生者；产生者为主管时升级',
                              'producer': allocation['producer'], 'qualified_approvers': approvers})
        timestamp = now()
        request_id = digest(['approval', ticket_id, ticket['current_round'], 'REVERSAL',
                             allocation['allocation_id'], request_key])
        db.execute('INSERT INTO approval_request VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (request_id, 'REVERSAL', ticket_id, ticket['current_round'], cancel_line_id,
                    allocation['allocation_id'], actor, reason.strip(), 'OPEN', None, '', escalation, None,
                    timestamp, request_key))
        outcome = {'request_id': request_id, 'status': 'OPEN', 'action': 'REVERSAL',
                   'allocation_id': allocation['allocation_id'], 'escalation': json.loads(escalation)}
        return _record(db, request_key, payload, outcome, action='REQUEST_REVERSAL', object_id=ticket_id, actor=actor,
                       before={'allocation_active': True, 'allocation_id': allocation['allocation_id']}, after=outcome,
                       reason=reason.strip(), timestamp=timestamp)
    return _write(database, work)


def decide_reversal(database, *, request_id, actor, approve, note='', request_key, actors=None):
    config = _config(actors)
    def work(db):
        payload = digest(['reversal-decide', request_id, actor, bool(approve), note.strip()])
        hit, outcome = _replay(db, request_key, payload)
        if hit:
            return outcome
        _actor(config, actor)
        if not accounts.has_permission(config, actor, 'APPROVE_REVERSAL'):
            raise WorkflowRejected(f'NO_PERMISSION: {actor} 缺少 APPROVE_REVERSAL（批准冲销）权限')
        request = db.execute('SELECT * FROM approval_request WHERE request_id=?', (request_id,)).fetchone()
        if not request or request['action'] != 'REVERSAL':
            raise WorkflowRejected(f'REQUEST_NOT_FOUND: {request_id}')
        if request['status'] != 'OPEN':
            raise WorkflowRejected(f'REQUEST_NOT_OPEN: 该申请已是 {request["status"]}')
        allocation = db.execute('SELECT * FROM allocation WHERE allocation_id=?', (request['allocation_id'],)).fetchone()
        if not allocation or not allocation['active']:
            raise WorkflowRejected('OBJECT_STATE_CHANGED: 该配对已被冲销或不再有效，审批依据已变化')
        producer_actor = allocation['producer'].split(':', 1)[1] if str(allocation['producer']).startswith('ACTOR:') else ''
        if producer_actor and actor == producer_actor:
            raise WorkflowRejected('SELF_APPROVAL_FORBIDDEN: 批准人不能是配对产生者（产生者为主管时按升级路径转其上级或其他合格主管）')
        ticket = _ticket(db, request['ticket_id'])
        timestamp = now()
        transition = None
        if approve:
            reverse_in_transaction(db, allocation_id=allocation['allocation_id'], producer='APPROVAL:' + actor,
                                   request_key='approve-reversal:' + request_id, reason=request['reason'])
            if request['cancel_line_id'] is not None:
                db.execute('''UPDATE conclusion_version SET status='INVALIDATED', invalidated_at=?, invalid_reason=?
                              WHERE ticket_id=? AND cancel_line_id=? AND original_line_id=? AND conclusion_type='PAIRING'
                                AND status='SUBMITTED' ''',
                           (timestamp, '获批冲销：' + (request['reason'] or ''), request['ticket_id'],
                            request['cancel_line_id'], allocation['original_line_id']))
                transition = _open_followup(db, ticket, [request['cancel_line_id']], event='REVERSAL_APPROVED',
                                            timestamp=timestamp)
                transition['before'] = _ticket_state(ticket)
        db.execute('UPDATE approval_request SET status=?,approver=?,decision_note=?,decided_at=? WHERE request_id=?',
                   ('APPROVED' if approve else 'REJECTED', actor, note.strip(), timestamp, request_id))
        outcome = {'request_id': request_id, 'action': 'REVERSAL', 'status': 'APPROVED' if approve else 'REJECTED',
                   'allocation_id': allocation['allocation_id'], 'ticket_id': request['ticket_id'],
                   'transition': transition}
        return _record(db, request_key, payload, outcome, action='DECIDE_REVERSAL', object_id=request['ticket_id'],
                       actor=actor, before={'request': 'OPEN', 'allocation_active': True}, after=outcome,
                       reason=note.strip() or ('批准冲销' if approve else '不批准冲销'), timestamp=timestamp)
    return _write(database, work)
