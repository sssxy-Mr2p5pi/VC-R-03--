import sqlite3
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_dataset (
 dataset_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL UNIQUE, source_name TEXT NOT NULL,
 range_json TEXT NOT NULL, epoch TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_snapshot (
 snapshot_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES source_dataset,
 rule_hash TEXT NOT NULL, dictionary_version TEXT NOT NULL, dictionary_hash TEXT NOT NULL,
 normalization_version TEXT NOT NULL, read_version TEXT NOT NULL, rules_json TEXT NOT NULL,
 status TEXT NOT NULL, summary_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS active_analysis (
 dataset_id TEXT PRIMARY KEY REFERENCES source_dataset,
 snapshot_id TEXT NOT NULL REFERENCES analysis_snapshot);
CREATE TABLE IF NOT EXISTS source_line (
 line_id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES source_dataset,
 sheet TEXT NOT NULL, excel_row INTEGER NOT NULL, raw_json TEXT NOT NULL,
 UNIQUE(dataset_id, sheet, excel_row));
CREATE TABLE IF NOT EXISTS line_analysis (
 snapshot_id TEXT NOT NULL REFERENCES analysis_snapshot,
 line_id INTEGER NOT NULL REFERENCES source_line, invoice_key TEXT, stock_key TEXT, customer_key TEXT,
 normalization_version TEXT NOT NULL, side TEXT NOT NULL, line_kind TEXT NOT NULL,
 quantity INTEGER, price TEXT, invoice_date TEXT, disposition TEXT NOT NULL,
 exclusion_primary TEXT, exclusion_tags TEXT NOT NULL, retained_line_id INTEGER REFERENCES source_line,
 reason_category TEXT, primary_reason TEXT, quality_tags TEXT NOT NULL,
 candidate_comparable INTEGER NOT NULL, PRIMARY KEY(snapshot_id, line_id));
CREATE TABLE IF NOT EXISTS key_variant (
 snapshot_id TEXT NOT NULL REFERENCES analysis_snapshot, field TEXT NOT NULL, key TEXT NOT NULL,
 raw_json TEXT NOT NULL, narrow_key TEXT NOT NULL, occurrences INTEGER NOT NULL,
 first_line_id INTEGER NOT NULL REFERENCES source_line, suspicious INTEGER NOT NULL,
 PRIMARY KEY(snapshot_id,field,key,raw_json));
CREATE TABLE IF NOT EXISTS import_run (
 run_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES analysis_snapshot,
 finished_at TEXT NOT NULL, reused INTEGER NOT NULL, elapsed_seconds REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_analysis_invoice ON line_analysis(snapshot_id, invoice_key);
CREATE INDEX IF NOT EXISTS ix_analysis_stock ON line_analysis(snapshot_id, stock_key);
CREATE INDEX IF NOT EXISTS ix_analysis_customer ON line_analysis(snapshot_id, customer_key);
CREATE INDEX IF NOT EXISTS ix_analysis_view ON line_analysis(snapshot_id, disposition, reason_category, line_id);
'''


def connect(path: Path, *, initialize=False):
    if initialize:
        path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if initialize:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(SCHEMA)
    return db
