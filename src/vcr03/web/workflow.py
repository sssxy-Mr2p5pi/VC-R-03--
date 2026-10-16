"""C manual-workflow pages: ticket processing, approvals and metrics.

Teaching-only account switching: the selected account lives in the session and is
passed to the workflow service, which enforces the real permission/avoidance rules.
"""
import hmac
import secrets

from flask import abort, redirect, render_template, request, session, url_for

from vcr03.workflow import actors as accounts
from vcr03.workflow import query, service
from vcr03.workflow.allocation import AllocationRejected

REJECTIONS = (service.WorkflowRejected, AllocationRejected)


def register(app):
    def context():
        selected = request.values.get('source', 'full')
        if selected not in app.config['SOURCES']:
            abort(400)
        return selected, app.config['DATABASES'][selected]

    def doc():
        return accounts.load()

    def current_actor(config):
        ids = [a['id'] for a in config['actors']]
        chosen = (request.values.get('actor') or '').strip()
        if chosen:
            if chosen not in ids:
                abort(400)
            session['actor'] = chosen
        if session.get('actor') not in ids:
            session['actor'] = ids[0]
        return session['actor']

    def guard():
        if not session.get('csrf') or not hmac.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400)

    def render_ticket(selected, database, ticket_id, actor, config, error=None, ok=None):
        detail = query.ticket_detail(database, ticket_id)
        if detail is None:
            abort(404)
        ticket = detail['ticket']
        lines = detail.get('round_lines') or detail['todo']
        pending, drafts, reviews, active = {}, {}, {}, {}
        for item in detail['conclusions']:
            if item['round_no'] != ticket['current_round']:
                continue
            if item['status'] == 'SUBMITTED':
                pending[item['cancel_line_id']] = item
            elif item['status'] == 'DRAFT' and item['submitter'] == actor:
                drafts[item['cancel_line_id']] = item
        for item in detail['reviews']:
            if item['round_no'] == ticket['current_round']:
                reviews[item['cancel_line_id']] = item
        for item in detail['allocations']:
            if item['active']:
                active[item['cancel_line_id']] = item
        view = []
        for line in lines:
            lid = line['cancel_line_id']
            row = {**line, 'pending': pending.get(lid), 'draft': drafts.get(lid),
                   'allocation': active.get(lid), 'last_review': reviews.get(lid)}
            allocation = row['allocation']
            row['auto_review'] = bool(allocation and str(allocation['producer']).startswith('SYSTEM:') and not row['last_review'])
            view.append(row)
        session.setdefault('csrf', secrets.token_hex(32))
        open_reversal = {item['allocation_id'] for item in detail['approvals']
                         if item['status'] == 'OPEN' and item['action'] == 'REVERSAL' and item['allocation_id']}
        open_release = [item for item in detail['approvals'] if item['status'] == 'OPEN' and item['action'] == 'RELEASE']
        return render_template('ticket_detail.html', selected=selected, detail=detail, lines=view,
                               actor=actor, actors=config['actors'], csrf=session['csrf'],
                               make_key=secrets.token_hex, error=error, ok=ok,
                               open_reversal=open_reversal, open_release=open_release)

    def action(ticket_id, handler, fields=(), **extra):
        guard()
        selected, database = context()
        config = doc()
        actor = current_actor(config)
        values = {name: request.form.get(name, '') for name in fields}
        for name, raw in values.items():
            if raw == '':
                values[name] = None if name == 'original_line_id' else raw
        if values.get('original_line_id') is not None:
            try:
                values['original_line_id'] = int(values['original_line_id'])
            except ValueError:
                abort(400)
        request_key = request.form.get('request_key', '').strip() or secrets.token_hex(16)
        try:
            outcome = handler(database, ticket_id=ticket_id, actor=actor, request_key=request_key,
                              actors=config, **values, **extra)
        except REJECTIONS as exc:
            return render_ticket(selected, database, ticket_id, actor, config, error=str(exc)), 409
        return redirect(url_for('workflow_ticket', ticket_id=ticket_id, source=selected,
                                ok=outcome.get('status', outcome.get('request_id', 'done'))))

    @app.get('/workflow/ticket/<ticket_id>')
    def workflow_ticket(ticket_id):
        selected, database = context()
        config = doc()
        actor = current_actor(config)
        return render_ticket(selected, database, ticket_id, actor, config, ok=request.args.get('ok'))

    @app.post('/workflow/ticket/<ticket_id>/dispatch')
    def workflow_dispatch(ticket_id):
        return action(ticket_id, service.dispatch, fields=('assignee',))

    @app.post('/workflow/ticket/<ticket_id>/line/<int:cancel_line_id>/draft')
    def workflow_draft(ticket_id, cancel_line_id):
        return action(ticket_id, service.save_draft, fields=('conclusion_type', 'original_line_id', 'note'),
                      cancel_line_id=cancel_line_id)

    @app.post('/workflow/ticket/<ticket_id>/line/<int:cancel_line_id>/cancel-draft')
    def workflow_cancel_draft(ticket_id, cancel_line_id):
        return action(ticket_id, service.cancel_draft, cancel_line_id=cancel_line_id)

    @app.post('/workflow/ticket/<ticket_id>/line/<int:cancel_line_id>/submit')
    def workflow_submit(ticket_id, cancel_line_id):
        return action(ticket_id, service.submit_conclusion, fields=('conclusion_type', 'original_line_id', 'note'),
                      cancel_line_id=cancel_line_id)

    @app.post('/workflow/ticket/<ticket_id>/line/<int:cancel_line_id>/review')
    def workflow_review(ticket_id, cancel_line_id):
        return action(ticket_id, service.review_line, fields=('verdict', 'reason'), cancel_line_id=cancel_line_id)

    @app.post('/workflow/ticket/<ticket_id>/line/<int:cancel_line_id>/reversal-request')
    def workflow_reversal_request(ticket_id, cancel_line_id):
        return action(ticket_id, service.request_reversal, fields=('reason',), cancel_line_id=cancel_line_id)

    @app.post('/workflow/ticket/<ticket_id>/close')
    def workflow_close(ticket_id):
        return action(ticket_id, service.close_ticket, fields=('note',))

    @app.post('/workflow/ticket/<ticket_id>/release-request')
    def workflow_release_request(ticket_id):
        return action(ticket_id, service.request_release, fields=('reason',))

    @app.post('/workflow/approval/<request_id>/release')
    def workflow_release_decide(request_id):
        guard()
        selected, database = context()
        config = doc()
        actor = current_actor(config)
        ticket_id = request.form.get('ticket_id', '')
        try:
            outcome = service.decide_release(database, request_id=request_id, actor=actor,
                                             approve=request.form.get('approve') == '1',
                                             note=request.form.get('note', ''),
                                             request_key=request.form.get('request_key', '') or secrets.token_hex(16),
                                             actors=config)
        except REJECTIONS as exc:
            return render_ticket(selected, database, ticket_id, actor, config, error=str(exc)), 409
        return redirect(url_for('workflow_ticket', ticket_id=ticket_id, source=selected,
                                ok=outcome.get('status', 'done')))

    @app.post('/workflow/approval/<request_id>/reversal')
    def workflow_reversal_decide(request_id):
        guard()
        selected, database = context()
        config = doc()
        actor = current_actor(config)
        ticket_id = request.form.get('ticket_id', '')
        try:
            outcome = service.decide_reversal(database, request_id=request_id, actor=actor,
                                              approve=request.form.get('approve') == '1',
                                              note=request.form.get('note', ''),
                                              request_key=request.form.get('request_key', '') or secrets.token_hex(16),
                                              actors=config)
        except REJECTIONS as exc:
            return render_ticket(selected, database, ticket_id, actor, config, error=str(exc)), 409
        return redirect(url_for('workflow_ticket', ticket_id=ticket_id, source=selected,
                                ok=outcome.get('status', 'done')))

    @app.get('/metrics')
    def metrics():
        selected, database = context()
        return render_template('metrics.html', selected=selected, metrics=query.metrics(database))
