"""C pages: ticket processing, approval endpoints, metrics and guards (synthetic fixtures)."""
from contextlib import closing

from conftest import line
from vcr03.matching import service as matching
from vcr03.storage.database import connect
from vcr03.web import create_app


def build(workbook, tmp_path, rows):
    source = workbook(rows)
    db = tmp_path / 'web.sqlite'
    matching.reconcile_workbook(source, db)
    app = create_app({'TESTING': True, 'SOURCES': {'full': source}, 'DATABASES': {'full': db}})
    return app, db, source


def stored(db, table):
    with closing(connect(db)) as connection:
        return [dict(r) for r in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]


def csrf_of(client):
    with client.session_transaction() as session:
        return session['csrf']


def test_workflow_pages_full_flow(workbook, tmp_path):
    app, db, _ = build(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    client = app.test_client()
    ticket = stored(db, 'ticket')[0]
    t = ticket['ticket_id']
    cid = stored(db, 'ticket_line')[0]['cancel_line_id']

    page = client.get(f'/workflow/ticket/{t}?source=full')
    assert page.status_code == 200 and '工单处理' in page.get_data(as_text=True)
    assert '派单' in page.get_data(as_text=True)
    csrf = csrf_of(client)

    posted = client.post(f'/workflow/ticket/{t}/dispatch', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w1', 'assignee': 'finance_li'})
    assert posted.status_code == 302
    assert stored(db, 'ticket')[0]['status'] == 'IN_PROGRESS'
    assert stored(db, 'ticket')[0]['assignee'] == 'finance_li'
    assert stored(db, 'assignment')[0]['actor_id'] == 'finance_li'

    switch = client.get(f'/workflow/ticket/{t}?source=full&actor=finance_li')
    assert switch.status_code == 200 and '归属操作' in switch.get_data(as_text=True)
    submitted = client.post(f'/workflow/ticket/{t}/line/{cid}/submit', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w2', 'conclusion_type': 'PAIRING',
        'original_line_id': '1', 'note': '价差核查：按候选1配对'})
    assert submitted.status_code == 302
    allocation = stored(db, 'allocation')[0]
    assert allocation['producer'] == 'ACTOR:finance_li' and allocation['quantity'] == 4

    rejected = client.post(f'/workflow/ticket/{t}/close', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w3', 'note': '   '})
    assert rejected.status_code == 409 and 'NOTE_REQUIRED' in rejected.get_data(as_text=True)
    closed = client.post(f'/workflow/ticket/{t}/close', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w4', 'note': '核对完成'})
    assert closed.status_code == 302 and stored(db, 'ticket')[0]['status'] == 'CLOSED'

    client.get(f'/workflow/ticket/{t}?source=full&actor=warehouse_lead')
    bad = client.post(f'/workflow/ticket/{t}/line/{cid}/review', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w5', 'verdict': 'FAIL', 'reason': ''})
    assert bad.status_code == 409 and 'REASON_REQUIRED' in bad.get_data(as_text=True)
    failed = client.post(f'/workflow/ticket/{t}/line/{cid}/review', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w6', 'verdict': 'FAIL', 'reason': '价差解释不成立'})
    assert failed.status_code == 302
    assert stored(db, 'ticket')[0]['status'] == 'PENDING' and stored(db, 'ticket')[0]['current_round'] == 2
    assert stored(db, 'allocation')[0]['active'] == 0

    client.get(f'/workflow/ticket/{t}?source=full&actor=manager_sun')
    redispatched = client.post(f'/workflow/ticket/{t}/dispatch', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w7', 'assignee': 'finance_li'})
    assert redispatched.status_code == 302
    client.get(f'/workflow/ticket/{t}?source=full&actor=finance_li')
    nonpair = client.post(f'/workflow/ticket/{t}/line/{cid}/submit', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w8', 'conclusion_type': 'NON_PAIRING',
        'original_line_id': '', 'note': '重查后不配对'})
    assert nonpair.status_code == 302
    released = client.post(f'/workflow/ticket/{t}/release-request', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w9', 'reason': '转交处理'})
    assert released.status_code == 302
    request = [r for r in stored(db, 'approval_request') if r['action'] == 'RELEASE'][0]
    assert request['status'] == 'OPEN'

    client.get(f'/workflow/ticket/{t}?source=full&actor=supervisor_zhao')
    approved = client.post(f'/workflow/approval/{request["request_id"]}/release', data={
        'source': 'full', 'csrf': csrf, 'request_key': 'w10', 'ticket_id': t, 'approve': '1', 'note': '同意'})
    assert approved.status_code == 302
    assert stored(db, 'ticket')[0]['status'] == 'PENDING' and stored(db, 'ticket')[0]['assignee'] is None

    metrics = client.get('/metrics?source=full')
    text = metrics.get_data(as_text=True)
    assert metrics.status_code == 200 and '处理指标' in text and '复核否决' in text

    listing = client.get('/tickets?source=full')
    assert listing.status_code == 200 and f'/workflow/ticket/{t}' in listing.get_data(as_text=True)


def test_workflow_pages_guard_and_untouched_tables(workbook, tmp_path):
    app, db, _ = build(workbook, tmp_path, [line(quantity=10), line('C1', quantity=-4, price=3)])
    client = app.test_client()
    ticket = stored(db, 'ticket')[0]
    page = client.get(f'/workflow/ticket/{ticket["ticket_id"]}?source=full')
    assert page.status_code == 200 and '派单' in page.get_data(as_text=True)
    no_csrf = client.post(f'/workflow/ticket/{ticket["ticket_id"]}/dispatch', data={
        'source': 'full', 'assignee': 'finance_li', 'request_key': 'x1'})
    assert no_csrf.status_code == 400
    assert stored(db, 'ticket')[0]['status'] == 'PENDING'
    with closing(connect(db)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'processing_todo' not in tables and 'assignment' not in tables
    metrics = client.get('/metrics?source=full')
    assert metrics.status_code == 200 and '处理指标' in metrics.get_data(as_text=True)
    bad_source = client.get(f'/workflow/ticket/{ticket["ticket_id"]}?source=nope')
    assert bad_source.status_code == 400
    assert client.get('/workflow/ticket/nope?source=full').status_code == 404
