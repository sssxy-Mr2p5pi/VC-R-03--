"""Additive C-workflow schema; A/B records stay untouched."""
from vcr03.storage.database import connect

SCHEMA = '''
CREATE TABLE IF NOT EXISTS assignment (
 assignment_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES ticket,
 actor_id TEXT NOT NULL, round_no INTEGER NOT NULL, started_at TEXT NOT NULL,
 ended_at TEXT, end_reason TEXT, active INTEGER NOT NULL CHECK(active IN (0,1)));
CREATE UNIQUE INDEX IF NOT EXISTS ix_active_assignment ON assignment(ticket_id) WHERE active=1;
CREATE TABLE IF NOT EXISTS conclusion_version (
 conclusion_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES ticket,
 cancel_line_id INTEGER NOT NULL REFERENCES source_line, round_no INTEGER NOT NULL,
 conclusion_type TEXT NOT NULL CHECK(conclusion_type IN ('PAIRING','NON_PAIRING')),
 original_line_id INTEGER REFERENCES source_line, note TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('DRAFT','SUBMITTED','INVALIDATED')),
 submitter TEXT NOT NULL, submitter_role_json TEXT NOT NULL, created_at TEXT NOT NULL,
 submitted_at TEXT, invalidated_at TEXT, invalid_reason TEXT NOT NULL DEFAULT '',
 request_key TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_conclusion_line ON conclusion_version(ticket_id,cancel_line_id,status);
CREATE TABLE IF NOT EXISTS review (
 review_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES ticket,
 cancel_line_id INTEGER NOT NULL REFERENCES source_line, round_no INTEGER NOT NULL,
 target_kind TEXT NOT NULL CHECK(target_kind IN ('CONCLUSION','AUTO_ALLOCATION')),
 conclusion_id TEXT, allocation_id TEXT,
 verdict TEXT NOT NULL CHECK(verdict IN ('PASS','FAIL')), reason TEXT NOT NULL DEFAULT '',
 reviewer TEXT NOT NULL, reviewer_role_json TEXT NOT NULL, submitter_snapshot TEXT NOT NULL,
 created_at TEXT NOT NULL, request_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approval_request (
 request_id TEXT PRIMARY KEY, action TEXT NOT NULL CHECK(action IN ('RELEASE','REVERSAL')),
 ticket_id TEXT NOT NULL REFERENCES ticket, round_no INTEGER NOT NULL,
 cancel_line_id INTEGER, allocation_id TEXT, applicant TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL CHECK(status IN ('OPEN','APPROVED','REJECTED')),
 approver TEXT, decision_note TEXT NOT NULL DEFAULT '', escalation TEXT NOT NULL DEFAULT '',
 decided_at TEXT, created_at TEXT NOT NULL, request_key TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_approval_open ON approval_request(ticket_id,action,status);
CREATE TABLE IF NOT EXISTS processing_todo (
 ticket_id TEXT NOT NULL REFERENCES ticket, round_no INTEGER NOT NULL,
 cancel_line_id INTEGER NOT NULL REFERENCES source_line,
 status TEXT NOT NULL CHECK(status IN ('OPEN','RESOLVED')),
 resolved_at TEXT, resolved_by_conclusion TEXT,
 PRIMARY KEY(ticket_id,round_no,cancel_line_id));
'''


def initialize(database):
    """Create the additive tables if absent; safe to call repeatedly on any database size."""
    db = connect(database)
    try:
        db.executescript(SCHEMA)
    finally:
        db.close()
