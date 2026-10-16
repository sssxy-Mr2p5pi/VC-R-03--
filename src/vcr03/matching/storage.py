"""Additive schema: A snapshots remain immutable."""
from vcr03.storage.database import connect

SCHEMA = '''
CREATE TABLE IF NOT EXISTS matching_snapshot (
 match_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES source_dataset,
 snapshot_id TEXT NOT NULL REFERENCES analysis_snapshot, rule_version TEXT NOT NULL,
 rules_json TEXT NOT NULL, status TEXT NOT NULL, summary_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(snapshot_id,rule_version));
CREATE TABLE IF NOT EXISTS active_matching (
 dataset_id TEXT PRIMARY KEY REFERENCES source_dataset,
 match_id TEXT NOT NULL REFERENCES matching_snapshot);
CREATE TABLE IF NOT EXISTS match_result (
 match_id TEXT NOT NULL REFERENCES matching_snapshot, cancel_line_id INTEGER NOT NULL REFERENCES source_line,
 invoice_key TEXT NOT NULL, classification TEXT NOT NULL,
 candidate_computed INTEGER NOT NULL, candidate_count INTEGER,
 original_line_id INTEGER REFERENCES source_line, requested_quantity INTEGER,
 price_delta TEXT, primary_reason TEXT, reason_categories TEXT NOT NULL,
 reason_tags TEXT NOT NULL, own_quality INTEGER NOT NULL, candidate_quality INTEGER NOT NULL,
 PRIMARY KEY(match_id,cancel_line_id));
CREATE TABLE IF NOT EXISTS candidate (
 match_id TEXT NOT NULL, cancel_line_id INTEGER NOT NULL, original_line_id INTEGER NOT NULL REFERENCES source_line,
 price_delta TEXT, quantity_delta INTEGER NOT NULL, quality_json TEXT NOT NULL,
 PRIMARY KEY(match_id,cancel_line_id,original_line_id),
 FOREIGN KEY(match_id,cancel_line_id) REFERENCES match_result);
CREATE TABLE IF NOT EXISTS quota_demand (
 match_id TEXT NOT NULL REFERENCES matching_snapshot, original_line_id INTEGER NOT NULL REFERENCES source_line,
 original_quantity INTEGER NOT NULL, requested_quantity INTEGER NOT NULL,
 claim_count INTEGER NOT NULL, over_quota INTEGER NOT NULL, PRIMARY KEY(match_id,original_line_id));
CREATE TABLE IF NOT EXISTS invoice_result (
 match_id TEXT NOT NULL REFERENCES matching_snapshot, invoice_key TEXT NOT NULL,
 classification TEXT NOT NULL, summary_json TEXT NOT NULL, PRIMARY KEY(match_id,invoice_key));
CREATE TABLE IF NOT EXISTS ticket (
 ticket_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES source_dataset, invoice_key TEXT NOT NULL,
 ticket_type TEXT NOT NULL CHECK(ticket_type IN ('DATA_QUALITY','MATCHING_DISCREPANCY','MIXED')),
 status TEXT NOT NULL CHECK(status IN ('PENDING','IN_PROGRESS','CLOSED')),
 assignee TEXT, current_round INTEGER NOT NULL, version INTEGER NOT NULL,
 initial_match_id TEXT NOT NULL REFERENCES matching_snapshot, created_at TEXT NOT NULL,
 UNIQUE(dataset_id,invoice_key));
CREATE TABLE IF NOT EXISTS processing_round (
 ticket_id TEXT NOT NULL REFERENCES ticket, round_no INTEGER NOT NULL,
 ticket_type_snapshot TEXT NOT NULL, reasons_json TEXT NOT NULL, type_rule_version TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(ticket_id,round_no));
CREATE TABLE IF NOT EXISTS ticket_line (
 ticket_id TEXT NOT NULL REFERENCES ticket, cancel_line_id INTEGER NOT NULL REFERENCES source_line,
 initial_match_id TEXT NOT NULL REFERENCES matching_snapshot, initial_pending INTEGER NOT NULL,
 PRIMARY KEY(ticket_id,cancel_line_id));
CREATE TABLE IF NOT EXISTS allocation (
 allocation_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES source_dataset,
 match_id TEXT NOT NULL REFERENCES matching_snapshot,
 cancel_line_id INTEGER NOT NULL REFERENCES source_line, original_line_id INTEGER NOT NULL REFERENCES source_line,
 quantity INTEGER NOT NULL CHECK(quantity>0), active INTEGER NOT NULL CHECK(active IN (0,1)),
 producer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ix_active_allocation ON allocation(dataset_id,cancel_line_id) WHERE active=1;
CREATE TABLE IF NOT EXISTS ledger_entry (
 entry_id TEXT PRIMARY KEY, allocation_id TEXT NOT NULL REFERENCES allocation,
 original_line_id INTEGER NOT NULL REFERENCES source_line,
 delta INTEGER NOT NULL CHECK(delta<>0), reverses_entry_id TEXT UNIQUE REFERENCES ledger_entry,
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ledger_original ON ledger_entry(original_line_id);
CREATE TABLE IF NOT EXISTS operation_receipt (
 request_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_event (
 event_id TEXT PRIMARY KEY, action TEXT NOT NULL, object_id TEXT NOT NULL,
 actor TEXT NOT NULL, request_key TEXT NOT NULL, before_json TEXT NOT NULL,
 after_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_result_filter ON match_result(match_id,classification,primary_reason,cancel_line_id);
CREATE INDEX IF NOT EXISTS ix_candidate_original ON candidate(match_id,original_line_id);
CREATE INDEX IF NOT EXISTS ix_ticket_filter ON ticket(dataset_id,ticket_type,status);
'''


def initialize(database):
    db = connect(database)
    try:
        db.executescript(SCHEMA)
    finally:
        db.close()
