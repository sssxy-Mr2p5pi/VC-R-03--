"""C manual-workflow contracts on synthetic fixtures; acceptance numbering per 怎么算做对.md."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import threading

import pytest
from conftest import line
from vcr03.matching import service as matching
from vcr03.storage.database import connect
from vcr03.workflow import query as wquery
from vcr03.workflow import service as workflow
from vcr03.workflow.allocation import AllocationRejected

ACTORS = {'actors': [
    {'id': 'clerk_lu', 'name': '陆明', 'business_role': 'aftersales', 'supervisor': 'sup_qin', 'permissions': []},
    {'id': 'sup_qin', 'name': '秦岚', 'business_role': 'aftersales', 'supervisor': 'mgr_sun', 'permissions': ['DISPATCH']},
    {'id': 'fin_li', 'name': '李慧', 'business_role': 'finance', 'supervisor': 'fin_zhao', 'permissions': ['REVIEW']},
    {'id': 'fin_ma', 'name': '马强', 'business_role': 'finance', 'supervisor': 'fin_zhao', 'permissions': ['REVIEW']},
    {'id': 'fin_zhao', 'name': '赵刚', 'business_role': 'finance', 'supervisor': 'mgr_sun',
     'permissions': ['REVIEW', 'APPROVE_RELEASE', 'APPROVE_REVERSAL']},
    {'id': 'mgr_sun', 'name': '孙立', 'business_role': 'management', 'supervisor': None,
     'permissions': ['DISPATCH', 'REVIEW', 'APPROVE_RELEASE', 'APPROVE_REVERSAL']},
    {'id': 'aud_gu', 'name': '顾审', 'business_role': 'audit', 'supervisor': 'mgr_sun', 'permissions': ['REVIEW']},
    {'id': 'whr_wang', 'name': '王仓', 'business_role': 'warehouse', 'supervisor': 'mgr_sun', 'permissions': []},
]}


def scene(workbook, tmp_path, rows, name='c.sqlite'):
    db = tmp_path / name
    matching.reconcile_workbook(workbook(rows), db)
    return db


def stored(db, table):
    with closing(connect(db)) as connection:
        return [dict(r) for r in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]


def tk(db):
    rows = stored(db, 'ticket')
    assert len(rows) == 1, rows
    return rows[0]


def lines_of(db, ticket_id):
    with closing(connect(db)) as connection:
        return [dict(r) for r in connection.execute(
            'SELECT * FROM ticket_line WHERE ticket_id=? ORDER BY cancel_line_id', (ticket_id,))]


def open_todos(db, ticket_id, round_no):
    with closing(connect(db)) as connection:
        return [dict(r) for r in connection.execute(
            "SELECT * FROM processing_todo WHERE ticket_id=? AND round_no=? AND status='OPEN' ORDER BY cancel_line_id",
            (ticket_id, round_no))]


def pair(db, ticket_id, cancel_line_id, actor, original_line_id, key, note='人工配对核查'):
    return workflow.submit_conclusion(database=db, ticket_id=ticket_id, cancel_line_id=cancel_line_id, actor=actor,
                                      conclusion_type='PAIRING', original_line_id=original_line_id, note=note,
                                      request_key=key, actors=ACTORS)


def test_tc04_dispatch_creates_single_holder(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)
    with pytest.raises(workflow.WorkflowRejected, match='NO_PERMISSION'):
        workflow.dispatch(database=db, ticket_id=t['ticket_id'], assignee='clerk_lu', actor='fin_li',
                          request_key='d0', actors=ACTORS)
    outcome = workflow.dispatch(database=db, ticket_id=t['ticket_id'], assignee='clerk_lu', actor='sup_qin',
                                request_key='d1', actors=ACTORS)
    assert outcome['status'] == 'IN_PROGRESS' and outcome['assignee'] == 'clerk_lu'
    assert tk(db)['status'] == 'IN_PROGRESS' and tk(db)['assignee'] == 'clerk_lu'
    assignments = stored(db, 'assignment')
    assert len(assignments) == 1 and assignments[0]['actor_id'] == 'clerk_lu' and assignments[0]['active'] == 1


def test_tc05_repeat_dispatch_keeps_first_holder(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='d1', actors=ACTORS)
    with pytest.raises(workflow.WorkflowRejected) as err:
        workflow.dispatch(database=db, ticket_id=t, assignee='mgr_sun', actor='mgr_sun', request_key='d2', actors=ACTORS)
    assert 'TICKET_ALREADY_ASSIGNED' in str(err.value) and 'clerk_lu' in str(err.value)
    assert [a['actor_id'] for a in stored(db, 'assignment')] == ['clerk_lu']


def test_tc06_close_pending_is_rejected_without_change(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    before = (tk(db)['status'], tk(db)['version'], len(stored(db, 'ledger_entry')))
    with pytest.raises(workflow.WorkflowRejected, match='INVALID_STATE'):
        workflow.close_ticket(database=db, ticket_id=t, actor='clerk_lu', note='直接关', request_key='d1', actors=ACTORS)
    assert (tk(db)['status'], tk(db)['version'], len(stored(db, 'ledger_entry'))) == before


def test_tc07_tc09_flow_history_and_composition(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    cid = lines_of(db, t)[0]['cancel_line_id']
    workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='d1', actors=ACTORS)
    workflow.save_draft(database=db, ticket_id=t, cancel_line_id=cid, actor='clerk_lu', conclusion_type='PAIRING',
                        original_line_id=1, note='价格差异核查', request_key='d2', actors=ACTORS)
    pair(db, t, cid, 'clerk_lu', 1, 'd3')
    outcome = workflow.close_ticket(database=db, ticket_id=t, actor='clerk_lu', note='核对完成', request_key='d4',
                                    actors=ACTORS)
    assert outcome['status'] == 'CLOSED'
    assert tk(db)['status'] == 'CLOSED' and tk(db)['assignee'] == 'clerk_lu'
    assert stored(db, 'allocation')[0]['active'] == 1 and stored(db, 'ledger_entry')[0]['delta'] == 4
    assert [r['status'] for r in stored(db, 'processing_todo')] == ['RESOLVED']
    events = stored(db, 'audit_event')
    actions = [e['action'] for e in events]
    assert actions[0] == 'CREATE_TICKET' and 'DISPATCH' in actions
    assert actions[-2:] == ['SUBMIT_CONCLUSION', 'CLOSE_TICKET']
    dispatch_event = next(e for e in events if e['action'] == 'DISPATCH')
    assert dispatch_event['actor'] == 'sup_qin' and dispatch_event['created_at']
    assert '"status":"PENDING"' in dispatch_event['before_json'] and '"status":"IN_PROGRESS"' in dispatch_event['after_json']
    detail = wquery.ticket_detail(db, t)
    scoped = [e['event_id'] for e in events if e['object_id'] == t]
    assert {e['event_id'] for e in detail['events']} == set(scoped) and len(scoped) >= 4
    line_view = detail['round_lines'][0]
    assert line_view['status'] == 'RESOLVED' and line_view['primary_reason'] == 'PRICE_DIFFERENCE'
    assert line_view['reason_categories'] == ['MATCHING_DISCREPANCY'] and line_view['candidates'][0]['original_line_id'] == 1


def test_tc08_close_requires_note_and_all_lines_resolved(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3), line('C1', 'SKU2', -2)])
    t = tk(db)['ticket_id']
    cids = [r['cancel_line_id'] for r in lines_of(db, t)]
    workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='d1', actors=ACTORS)
    with pytest.raises(workflow.WorkflowRejected, match='NOTE_REQUIRED'):
        workflow.close_ticket(database=db, ticket_id=t, actor='clerk_lu', note='   ', request_key='d2', actors=ACTORS)
    pair(db, t, cids[0], 'clerk_lu', 1, 'd3')
    with pytest.raises(workflow.WorkflowRejected, match='OPEN_TODO_REMAINS'):
        workflow.close_ticket(database=db, ticket_id=t, actor='clerk_lu', note='只处理一行', request_key='d4', actors=ACTORS)
    workflow.submit_conclusion(database=db, ticket_id=t, cancel_line_id=cids[1], actor='clerk_lu',
                               conclusion_type='NON_PAIRING', note='库存不同，无法配对', request_key='d5', actors=ACTORS)
    assert workflow.close_ticket(database=db, ticket_id=t, actor='clerk_lu', note='两行都已核查', request_key='d6',
                                 actors=ACTORS)['status'] == 'CLOSED'


def test_tc23_release_requires_qualified_non_holder(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    cid = lines_of(db, t)[0]['cancel_line_id']
    workflow.dispatch(database=db, ticket_id=t, assignee='fin_zhao', actor='sup_qin', request_key='d1', actors=ACTORS)
    pair(db, t, cid, 'fin_zhao', 1, 'd2')
    audit_before = len(stored(db, 'audit_event'))
    workflow.request_release(database=db, ticket_id=t, actor='fin_zhao', reason='调岗', request_key='d3', actors=ACTORS)
    request = stored(db, 'approval_request')[0]
    assert request['status'] == 'OPEN' and request['applicant'] == 'fin_zhao'
    escalation = json.loads(request['escalation'])
    assert escalation['current_assignee'] == 'fin_zhao' and escalation['qualified_approvers'] == ['mgr_sun']
    with pytest.raises(workflow.WorkflowRejected, match='SELF_APPROVAL_FORBIDDEN'):
        workflow.decide_release(database=db, request_id=request['request_id'], actor='fin_zhao', approve=True,
                                note='', request_key='d4', actors=ACTORS)
    with pytest.raises(workflow.WorkflowRejected, match='NO_PERMISSION'):
        workflow.decide_release(database=db, request_id=request['request_id'], actor='whr_wang', approve=True,
                                note='', request_key='d5', actors=ACTORS)
    outcome = workflow.decide_release(database=db, request_id=request['request_id'], actor='mgr_sun', approve=True,
                                      note='同意释放', request_key='d6', actors=ACTORS)
    assert outcome['status'] == 'APPROVED'
    ticket = tk(db)
    assert ticket['status'] == 'PENDING' and ticket['assignee'] is None
    assert stored(db, 'assignment')[0]['end_reason'] == 'RELEASED'
    assert stored(db, 'allocation')[0]['active'] == 1 and stored(db, 'ledger_entry')[0]['delta'] == 4
    assert len(stored(db, 'audit_event')) == audit_before + 2


def test_tc24_review_avoidance_matrix_keeps_submitter(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    cid = lines_of(db, t)[0]['cancel_line_id']
    workflow.dispatch(database=db, ticket_id=t, assignee='fin_li', actor='sup_qin', request_key='d1', actors=ACTORS)
    pair(db, t, cid, 'fin_li', 1, 'd2')
    with pytest.raises(workflow.WorkflowRejected, match='SELF_REVIEW_FORBIDDEN'):
        workflow.review_line(database=db, ticket_id=t, cancel_line_id=cid, actor='fin_li', verdict='PASS', reason='',
                             request_key='d3', actors=ACTORS)
    with pytest.raises(workflow.WorkflowRejected, match='SAME_ROLE_REVIEW_FORBIDDEN'):
        workflow.review_line(database=db, ticket_id=t, cancel_line_id=cid, actor='fin_ma', verdict='PASS', reason='',
                             request_key='d4', actors=ACTORS)
    assert workflow.review_line(database=db, ticket_id=t, cancel_line_id=cid, actor='aud_gu', verdict='PASS',
                                reason='跨业务复核通过', request_key='d5', actors=ACTORS)['verdict'] == 'PASS'
    workflow.request_release(database=db, ticket_id=t, actor='fin_li', reason='', request_key='d6', actors=ACTORS)
    request_id = stored(db, 'approval_request')[0]['request_id']
    workflow.decide_release(database=db, request_id=request_id, actor='mgr_sun', approve=True, note='同意',
                            request_key='d7', actors=ACTORS)
    workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='d8', actors=ACTORS)
    assert stored(db, 'conclusion_version')[0]['submitter'] == 'fin_li'
    assert tk(db)['assignee'] == 'clerk_lu'


def test_tc25_partial_fail_reverses_only_failed_line(workbook, tmp_path):
    rows = [line(quantity=10), line(stock='SKU2', quantity=5, price=1), line('C1', quantity=-4, price=3),
            line('C1', 'SKU2', -2, 2)]
    db = scene(workbook, tmp_path, rows)
    t = tk(db)['ticket_id']
    cids = [r['cancel_line_id'] for r in lines_of(db, t)]
    workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='d1', actors=ACTORS)
    pair(db, t, cids[0], 'clerk_lu', 1, 'd2')
    workflow.submit_conclusion(database=db, ticket_id=t, cancel_line_id=cids[1], actor='clerk_lu',
                               conclusion_type='NON_PAIRING', note='数量口径不同', request_key='d3', actors=ACTORS)
    workflow.close_ticket(database=db, ticket_id=t, actor='clerk_lu', note='核查完成', request_key='d4', actors=ACTORS)
    round_one = stored(db, 'processing_round')[0]
    workflow.review_line(database=db, ticket_id=t, cancel_line_id=cids[1], actor='fin_li', verdict='PASS', reason='',
                         request_key='d5', actors=ACTORS)
    with pytest.raises(workflow.WorkflowRejected, match='REASON_REQUIRED'):
        workflow.review_line(database=db, ticket_id=t, cancel_line_id=cids[0], actor='fin_li', verdict='FAIL', reason=' ',
                             request_key='d6', actors=ACTORS)
    outcome = workflow.review_line(database=db, ticket_id=t, cancel_line_id=cids[0], actor='fin_li', verdict='FAIL',
                                   reason='价差解释不成立', request_key='d7', actors=ACTORS)
    assert outcome['transition']['round'] == 2
    ticket = tk(db)
    assert ticket['status'] == 'PENDING' and ticket['current_round'] == 2 and ticket['assignee'] is None
    assert stored(db, 'processing_round')[0] == round_one
    rounds = stored(db, 'processing_round')
    assert len(rounds) == 2 and 'REVIEW_FAILED' in rounds[1]['reasons_json']
    assert [r['cancel_line_id'] for r in open_todos(db, t, 2)] == [cids[0]]
    conclusions = {r['cancel_line_id']: r for r in stored(db, 'conclusion_version')}
    assert conclusions[cids[0]]['status'] == 'INVALIDATED'
    assert conclusions[cids[1]]['status'] == 'SUBMITTED'
    allocations = {r['cancel_line_id']: r for r in stored(db, 'allocation')}
    assert allocations[cids[0]]['active'] == 0
    ledger = stored(db, 'ledger_entry')
    assert sorted(r['delta'] for r in ledger) == [-4, 4]
    assert not open_todos(db, t, 1)


def test_tc26_repeat_reversal_writes_one_reverse_entry(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    cid = lines_of(db, t)[0]['cancel_line_id']
    workflow.dispatch(database=db, ticket_id=t, assignee='mgr_sun', actor='sup_qin', request_key='d1', actors=ACTORS)
    pair(db, t, cid, 'mgr_sun', 1, 'd2')
    workflow.close_ticket(database=db, ticket_id=t, actor='mgr_sun', note='核查完成', request_key='d3', actors=ACTORS)
    workflow.request_reversal(database=db, ticket_id=t, cancel_line_id=cid, actor='mgr_sun', reason='事后发现错误',
                              request_key='d4', actors=ACTORS)
    request_id = stored(db, 'approval_request')[0]['request_id']
    with pytest.raises(workflow.WorkflowRejected, match='SELF_APPROVAL_FORBIDDEN'):
        workflow.decide_reversal(database=db, request_id=request_id, actor='mgr_sun', approve=True, note='',
                                 request_key='d5', actors=ACTORS)
    first = workflow.decide_reversal(database=db, request_id=request_id, actor='fin_zhao', approve=True, note='同意',
                                     request_key='d6', actors=ACTORS)
    replay = workflow.decide_reversal(database=db, request_id=request_id, actor='fin_zhao', approve=True, note='同意',
                                      request_key='d6', actors=ACTORS)
    assert first == replay and first['status'] == 'APPROVED'
    ledger = stored(db, 'ledger_entry')
    assert [r['delta'] for r in ledger] == [4, -4]
    with pytest.raises(workflow.WorkflowRejected, match='NO_ACTIVE_ALLOCATION'):
        workflow.request_reversal(database=db, ticket_id=t, cancel_line_id=cid, actor='clerk_lu', reason='再冲一次',
                                  request_key='d7', actors=ACTORS)
    assert len(stored(db, 'ledger_entry')) == 2 and sum(r['delta'] for r in stored(db, 'ledger_entry')) == 0
    assert len(stored(db, 'approval_request')) == 1


def test_tc27a_closed_reopen_creates_round_keeping_old_snapshot(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    cid = lines_of(db, t)[0]['cancel_line_id']
    workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='d1', actors=ACTORS)
    pair(db, t, cid, 'clerk_lu', 1, 'd2')
    workflow.close_ticket(database=db, ticket_id=t, actor='clerk_lu', note='核查完成', request_key='d3', actors=ACTORS)
    round_one = stored(db, 'processing_round')[0]
    workflow.request_reversal(database=db, ticket_id=t, cancel_line_id=cid, actor='clerk_lu', reason='重开核查',
                              request_key='d4', actors=ACTORS)
    request_id = stored(db, 'approval_request')[0]['request_id']
    workflow.decide_reversal(database=db, request_id=request_id, actor='fin_zhao', approve=True, note='同意',
                             request_key='d5', actors=ACTORS)
    ticket = tk(db)
    assert (ticket['status'], ticket['current_round']) == ('PENDING', 2)
    assert stored(db, 'processing_round')[0] == round_one
    assert [r['round_no'] for r in stored(db, 'processing_round')] == [1, 2]
    assert stored(db, 'processing_round')[1]['ticket_type_snapshot'] == 'MATCHING_DISCREPANCY'
    assert [r['cancel_line_id'] for r in open_todos(db, t, 2)] == [cid]
    assert [r['status'] for r in stored(db, 'processing_todo')] == ['RESOLVED', 'OPEN']


def test_tc27b_pending_merge_updates_round_type_in_place(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    cid = lines_of(db, t)[0]['cancel_line_id']
    workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='d1', actors=ACTORS)
    pair(db, t, cid, 'clerk_lu', 1, 'd2')
    workflow.request_release(database=db, ticket_id=t, actor='clerk_lu', reason='', request_key='d3', actors=ACTORS)
    request_id = stored(db, 'approval_request')[0]['request_id']
    workflow.decide_release(database=db, request_id=request_id, actor='fin_zhao', approve=True, note='同意',
                            request_key='d4', actors=ACTORS)
    assert tk(db)['status'] == 'PENDING'
    workflow.request_reversal(database=db, ticket_id=t, cancel_line_id=cid, actor='clerk_lu', reason='售后撤回',
                              request_key='d5', actors=ACTORS)
    request_id = [r for r in stored(db, 'approval_request') if r['action'] == 'REVERSAL'][0]['request_id']
    workflow.decide_reversal(database=db, request_id=request_id, actor='fin_zhao', approve=True, note='同意',
                             request_key='d6', actors=ACTORS)
    ticket = tk(db)
    assert ticket['status'] == 'PENDING' and ticket['current_round'] == 1
    assert len(stored(db, 'processing_round')) == 1
    assert [r['cancel_line_id'] for r in open_todos(db, t, 1)] == [cid]
    decision = [e for e in stored(db, 'audit_event') if e['action'] == 'DECIDE_REVERSAL'][-1]
    assert '"before"' in decision['after_json'] and '"round":1' in decision['after_json']
    assert [r['delta'] for r in stored(db, 'ledger_entry')] == [4, -4]


def test_tc30_concurrent_dispatch_single_winner(workbook, tmp_path, monkeypatch):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    barrier = threading.Barrier(2)
    monkeypatch.setattr(workflow, '_race_barrier', lambda: barrier.wait(timeout=10))
    def attempt(assignee, key):
        try:
            workflow.dispatch(database=db, ticket_id=t, assignee=assignee, actor='sup_qin', request_key=key, actors=ACTORS)
            return 'OK'
        except workflow.WorkflowRejected as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda args: attempt(*args), [('clerk_lu', 'r1'), ('mgr_sun', 'r2')]))
    winners = [r for r in results if r == 'OK']
    assert len(winners) == 1
    loser = next(r for r in results if r != 'OK')
    assert 'TICKET_ALREADY_ASSIGNED' in loser
    assert sum(1 for a in stored(db, 'assignment') if a['active'] == 1) == 1


def test_tc30_concurrent_quota_single_winner(workbook, tmp_path, monkeypatch):
    rows = [line(quantity=10), line('C1', quantity=-6, price=3), line('C2', quantity=-6, price=3)]
    db = scene(workbook, tmp_path, rows)
    tickets = {r['invoice_key']: r for r in stored(db, 'ticket')}
    assert set(tickets) == {'C1', 'C2'}
    ids = {}
    for invoice, ticket in tickets.items():
        workflow.dispatch(database=db, ticket_id=ticket['ticket_id'], assignee='clerk_lu', actor='sup_qin',
                          request_key='d' + invoice, actors=ACTORS)
        ids[invoice] = (ticket['ticket_id'], lines_of(db, ticket['ticket_id'])[0]['cancel_line_id'])
    barrier = threading.Barrier(2)
    monkeypatch.setattr(workflow, '_race_barrier', lambda: barrier.wait(timeout=10))
    def attempt(invoice, key):
        ticket_id, cid = ids[invoice]
        try:
            pair(db, ticket_id, cid, 'clerk_lu', 1, key)
            return 'OK'
        except (workflow.WorkflowRejected, AllocationRejected) as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda args: attempt(*args), [('C1', 'q1'), ('C2', 'q2')]))
    assert sorted(r == 'OK' for r in results) == [False, True]
    loser = next(r for r in results if r != 'OK')
    assert 'ALLOCATION_INSUFFICIENT' in loser and 'available=4' in loser
    assert sum(r['delta'] for r in stored(db, 'ledger_entry')) == 6
    winner_results = [pair_result for pair_result in stored(db, 'allocation')]
    assert len(winner_results) == 1 and winner_results[0]['quantity'] == 6


def test_tc32_draft_quota_rechecked_and_changed(workbook, tmp_path):
    rows = [line(quantity=10), line('C1', quantity=-6, price=3), line('C2', quantity=-6, price=3)]
    db = scene(workbook, tmp_path, rows)
    tickets = {r['invoice_key']: r for r in stored(db, 'ticket')}
    first, second = tickets['C1'], tickets['C2']
    for key, ticket in [('d1', first), ('d2', second)]:
        workflow.dispatch(database=db, ticket_id=ticket['ticket_id'], assignee='clerk_lu', actor='sup_qin',
                          request_key=key, actors=ACTORS)
    cid1 = lines_of(db, first['ticket_id'])[0]['cancel_line_id']
    cid2 = lines_of(db, second['ticket_id'])[0]['cancel_line_id']
    workflow.save_draft(database=db, ticket_id=second['ticket_id'], cancel_line_id=cid2, actor='clerk_lu',
                        conclusion_type='PAIRING', original_line_id=1, note='先存草稿', request_key='d3', actors=ACTORS)
    pair(db, first['ticket_id'], cid1, 'clerk_lu', 1, 'd4')
    with pytest.raises(AllocationRejected, match='ALLOCATION_INSUFFICIENT'):
        pair(db, second['ticket_id'], cid2, 'clerk_lu', 1, 'd5')
    assert sum(r['delta'] for r in stored(db, 'ledger_entry')) == 6
    assert sorted(r['status'] for r in stored(db, 'conclusion_version')) == ['DRAFT', 'SUBMITTED']
    assert sorted(r['status'] for r in stored(db, 'processing_todo')) == ['OPEN', 'RESOLVED']


def test_tc33_no_original_close_without_fabricated_pairing(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-2, customer=None)])
    t = tk(db)
    assert t['ticket_type'] == 'DATA_QUALITY'
    cid = lines_of(db, t['ticket_id'])[0]['cancel_line_id']
    workflow.dispatch(database=db, ticket_id=t['ticket_id'], assignee='clerk_lu', actor='sup_qin', request_key='d1',
                      actors=ACTORS)
    workflow.submit_conclusion(database=db, ticket_id=t['ticket_id'], cancel_line_id=cid, actor='clerk_lu',
                               conclusion_type='NON_PAIRING', note='客户缺失且无可用原单，人工核查完成', request_key='d2',
                               actors=ACTORS)
    workflow.close_ticket(database=db, ticket_id=t['ticket_id'], actor='clerk_lu', note='核查无原单可配', request_key='d3',
                          actors=ACTORS)
    assert tk(db)['status'] == 'CLOSED'
    assert not stored(db, 'allocation') and not stored(db, 'ledger_entry')
    conclusion = stored(db, 'conclusion_version')[0]
    assert conclusion['conclusion_type'] == 'NON_PAIRING' and conclusion['status'] == 'SUBMITTED'


def test_replay_conflict_and_draft_cancel(workbook, tmp_path):
    db = scene(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    t = tk(db)['ticket_id']
    cid = lines_of(db, t)[0]['cancel_line_id']
    first = workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='r1', actors=ACTORS)
    replay = workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='sup_qin', request_key='r1', actors=ACTORS)
    assert first == replay and len(stored(db, 'assignment')) == 1
    assert len([e for e in stored(db, 'audit_event') if e['action'] == 'DISPATCH']) == 1
    with pytest.raises(workflow.WorkflowRejected, match='IDEMPOTENCY_KEY_CONFLICT'):
        workflow.dispatch(database=db, ticket_id=t, assignee='clerk_lu', actor='mgr_sun', request_key='r1', actors=ACTORS)
    draft = workflow.save_draft(database=db, ticket_id=t, cancel_line_id=cid, actor='clerk_lu',
                                conclusion_type='NON_PAIRING', note='先存草稿', request_key='r2', actors=ACTORS)
    cancel = workflow.cancel_draft(database=db, ticket_id=t, cancel_line_id=cid, actor='clerk_lu', request_key='r3',
                                   actors=ACTORS)
    assert cancel['status'] == 'CANCELLED' and not stored(db, 'conclusion_version')
    second = workflow.save_draft(database=db, ticket_id=t, cancel_line_id=cid, actor='clerk_lu',
                                 conclusion_type='NON_PAIRING', note='重存草稿', request_key='r4', actors=ACTORS)
    assert second['conclusion_id'] != draft['conclusion_id']


def test_metrics_and_detail_fallbacks(workbook, tmp_path):
    rows = [line(quantity=10), line('C1', quantity=-1), line('C1', quantity=-2, customer=None)]
    db = scene(workbook, tmp_path, rows)
    t = tk(db)
    lines = {r['cancel_line_id']: r for r in lines_of(db, t['ticket_id'])}
    auto_cid = next(r['cancel_line_id'] for r in lines.values() if r['initial_pending'] == 0)
    pend_cid = next(r['cancel_line_id'] for r in lines.values() if r['initial_pending'] == 1)
    detail = wquery.ticket_detail(db, t['ticket_id'])
    assert sorted((x['cancel_line_id'], x['status']) for x in detail['round_lines']) == [(auto_cid, 'AUTO'), (pend_cid, 'OPEN')]
    assert [x['cancel_line_id'] for x in detail['todo']] == [pend_cid]
    assert [r['round_no'] for r in detail['rounds']] == [1]
    workflow.dispatch(database=db, ticket_id=t['ticket_id'], assignee='clerk_lu', actor='sup_qin', request_key='m1',
                      actors=ACTORS)
    workflow.review_line(database=db, ticket_id=t['ticket_id'], cancel_line_id=auto_cid, actor='fin_li', verdict='FAIL',
                         reason='系统配对错误', request_key='m2', actors=ACTORS)
    workflow.dispatch(database=db, ticket_id=t['ticket_id'], assignee='clerk_lu', actor='sup_qin', request_key='m3',
                      actors=ACTORS)
    workflow.submit_conclusion(database=db, ticket_id=t['ticket_id'], cancel_line_id=auto_cid, actor='clerk_lu',
                               conclusion_type='NON_PAIRING', note='重查后不配对', request_key='m4', actors=ACTORS)
    detail = wquery.ticket_detail(db, t['ticket_id'])
    assert sorted(x['cancel_line_id'] for x in detail['round_lines']) == [auto_cid, pend_cid]
    metrics = wquery.metrics(db)
    assert metrics['reversed_auto_allocations'] == 1
    assert metrics['reviews_fail'] == 1 and metrics['reviews_pass'] == 0
    assert metrics['unreviewed_submitted'] == 1
    assert metrics['oldest_wait_seconds'] >= 0 and metrics['average_review_wait_seconds'] == 0
    empty = tmp_path / 'empty.sqlite'
    assert wquery.ticket_detail(empty, 'x') is None
    assert wquery.metrics(empty)['unreviewed_submitted'] == 0
