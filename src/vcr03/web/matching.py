"""Read-only result/work-ticket views and the fixed-input reconciliation action."""
import csv
import hmac
import io
import json

from flask import abort, jsonify, redirect, render_template, request, session, Response
from vcr03.ingest.service import ImportRejected
from vcr03.matching import query, service
from vcr03.workflow.allocation import AllocationRejected

LABELS = {'MATCHED_AUTO':'已配对（自动）','SUSPECTED':'疑似','NO_ORIGINAL':'无原单',
          'NONPRODUCT_ONLY':'纯非商品','DATA_QUALITY':'数据质量差异','MATCHING_DISCREPANCY':'配对差异',
          'MIXED':'混合差异','PENDING':'待处理','IN_PROGRESS':'处理中','CLOSED':'已关闭'}


def register(app):
    app.jinja_env.globals['labels'] = LABELS

    def context():
        selected = request.values.get('source','full')
        if selected not in app.config['SOURCES']:
            abort(400)
        return selected, app.config['DATABASES'][selected]

    def page(name='page'):
        try:
            return max(1,int(request.args.get(name,1)))
        except ValueError:
            abort(400)

    @app.post('/reconcile')
    def reconcile():
        selected, database = context()
        if not session.get('csrf') or not hmac.compare_digest(session['csrf'],request.form.get('csrf','')):
            abort(400)
        try:
            service.reconcile_workbook(app.config['SOURCES'][selected],database)
        except (ImportRejected,service.ReconciliationRejected,AllocationRejected,OSError) as exc:
            return render_template('error.html',message=str(exc),selected=selected),409
        return redirect('/matches?source='+selected)

    @app.get('/api/matching-summary')
    def matching_summary():
        _,database = context()
        return jsonify(query.overview(database) or {'has_data':False})

    @app.get('/matches')
    def matches():
        selected,database = context()
        overview = query.overview(database)
        data = query.results(database,page=page(),classification=request.args.get('classification',''),reason=request.args.get('reason','')) if overview else None
        return render_template('matches.html',selected=selected,overview=overview,data=data)

    @app.get('/matches/<int:lid>')
    def match_detail(lid):
        selected,database = context()
        if not query.overview(database):
            abort(404)
        item = query.detail(database,lid,candidate_page=page('candidate_page'))
        if item is None:
            abort(404)
        return render_template('match_detail.html',selected=selected,item=item)

    @app.get('/tickets')
    def ticket_list():
        selected,database = context()
        overview = query.overview(database)
        data = query.tickets(database,page=page(),ticket_type=request.args.get('ticket_type','')) if overview else None
        return render_template('tickets.html',selected=selected,data=data)

    @app.get('/invoices/<path:invoice>')
    def invoice_detail(invoice):
        selected,database = context()
        if not query.overview(database):
            abort(404)
        item = query.invoice_detail(database,invoice)
        if item is None:
            abort(404)
        return render_template('invoice_detail.html',selected=selected,item=item)

    @app.get('/exports/<kind>.csv')
    def export(kind):
        selected,database = context()
        if kind not in {'results','candidates','tickets'} or not query.overview(database):
            abort(404)
        classification,reason,ticket_type = (request.args.get(k,'') for k in ('classification','reason','ticket_type'))
        def generate():
            yield '\ufeff'
            output = io.StringIO()
            writer = None
            for row in query.export_rows(database,kind,classification=classification,reason=reason,ticket_type=ticket_type):
                if writer is None:
                    writer = csv.DictWriter(output,fieldnames=list(row))
                    writer.writeheader()
                writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
        return Response(generate(),mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition':f'attachment; filename="{selected}-{kind}.csv"'})
