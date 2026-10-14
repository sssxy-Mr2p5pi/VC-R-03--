"""Module A local-only verification pages."""
from __future__ import annotations

import hmac
import json
import secrets
from contextlib import closing

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from vcr03.cli import DATABASES, SOURCES
from vcr03.ingest import service
from vcr03.storage.database import connect


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), SOURCES=SOURCES.copy(), DATABASES=DATABASES.copy())
    if config:
        app.config.update(config)

    def mode():
        value = request.values.get('source', 'full')
        if value not in app.config['SOURCES']:
            abort(400)
        return value

    def current(selected):
        path = app.config['DATABASES'][selected]
        if not path.exists():
            return None
        with closing(connect(path)) as db:
            row = db.execute("SELECT a.* FROM analysis_snapshot a JOIN active_analysis x USING(snapshot_id) WHERE a.status='A_COMPLETE' LIMIT 1").fetchone()
            return dict(row) if row else None

    @app.get('/')
    def index():
        selected = mode()
        session.setdefault('csrf', secrets.token_hex(32))
        snapshot = current(selected)
        return render_template('index.html', selected=selected, snapshot=snapshot,
                               summary=json.loads(snapshot['summary_json']) if snapshot else None,
                               csrf=session['csrf'], error=request.args.get('error'))

    @app.post('/import')
    def import_fixed():
        selected = mode()
        if not session.get('csrf') or not hmac.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400)
        try:
            service.ingest_workbook(app.config['SOURCES'][selected], app.config['DATABASES'][selected])
        except (service.ImportRejected, OSError) as exc:
            return render_template('error.html', message=str(exc), selected=selected), 400
        return redirect(url_for('index', source=selected))

    @app.get('/api/summary')
    def api_summary():
        snapshot = current(mode())
        return jsonify({'snapshot_id': snapshot['snapshot_id'], **json.loads(snapshot['summary_json'])} if snapshot else {'has_data': False})

    @app.get('/lines')
    def lines():
        selected = mode()
        snapshot = current(selected)
        if not snapshot:
            return redirect(url_for('index', source=selected))
        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            abort(400)
        view = request.args.get('view', 'all')
        filters = {'all': '', 'quality': " AND a.disposition='RETAINED' AND a.reason_category IS NOT NULL",
                   'excluded': " AND a.disposition='EXCLUDED'", 'nonproduct': " AND a.disposition='RETAINED' AND a.line_kind='NONPRODUCT'"}
        if view not in filters:
            abort(400)
        where = ' WHERE a.snapshot_id=?' + filters[view]
        with closing(connect(app.config['DATABASES'][selected])) as db:
            total = db.execute('SELECT COUNT(*) FROM line_analysis a'+where, (snapshot['snapshot_id'],)).fetchone()[0]
            rows = db.execute('''SELECT s.sheet,s.excel_row,s.raw_json,a.* FROM line_analysis a
                                 JOIN source_line s USING(line_id)'''+where+' ORDER BY a.line_id LIMIT 50 OFFSET ?',
                              (snapshot['snapshot_id'], (page-1)*50)).fetchall()
            rows = [{**dict(row), 'raw': json.loads(row['raw_json'])} for row in rows]
        return render_template('lines.html', selected=selected, rows=rows, page=page, total=total,
                               view=view, pages=max(1, (total+49)//50))

    @app.get('/line/<int:line_id>')
    def line_detail(line_id):
        selected = mode()
        snapshot = current(selected)
        if not snapshot:
            abort(404)
        with closing(connect(app.config['DATABASES'][selected])) as db:
            row = db.execute('''SELECT s.sheet,s.excel_row,s.raw_json,a.* FROM line_analysis a
                                JOIN source_line s USING(line_id) WHERE a.snapshot_id=? AND a.line_id=?''',
                             (snapshot['snapshot_id'], line_id)).fetchone()
        if row is None:
            abort(404)
        return render_template('detail.html', selected=selected, row=row, raw=json.loads(row['raw_json']),
                               reasons=json.loads(row['quality_tags']), headers=service.HEADERS,
                               source_hash=json.loads(snapshot['summary_json'])['source_sha256'])

    return app
