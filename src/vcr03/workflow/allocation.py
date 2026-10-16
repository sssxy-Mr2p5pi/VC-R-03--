"""Ledger writes belong to the caller's serialized business transaction."""
import json
from vcr03.ingest.rules import compact, digest
from vcr03.ingest.service import now


class AllocationRejected(ValueError):
    pass


def available(db, original_line_id, original_quantity):
    used = db.execute('SELECT COALESCE(SUM(delta),0) FROM ledger_entry WHERE original_line_id=?',
                      (original_line_id,)).fetchone()[0]
    return original_quantity-used


def allocate_in_transaction(db, *, match_id, cancel_line_id, original_line_id, quantity, producer, request_key):
    if not db.in_transaction:
        raise AllocationRejected('WRITE_TRANSACTION_REQUIRED')
    payload = digest([match_id, cancel_line_id, original_line_id, quantity, producer])
    receipt = db.execute('SELECT * FROM operation_receipt WHERE request_key=?', (request_key,)).fetchone()
    if receipt:
        if receipt['payload_hash'] != payload:
            raise AllocationRejected('IDEMPOTENCY_KEY_CONFLICT')
        return json.loads(receipt['result_json'])
    match = db.execute('SELECT * FROM matching_snapshot WHERE match_id=?', (match_id,)).fetchone()
    if not match or match['status'] not in {'COMMITTING', 'ACTIVE'}:
        raise AllocationRejected('MATCH_VERSION_NOT_ACTIVE')
    active = db.execute('SELECT snapshot_id FROM active_analysis WHERE dataset_id=?', (match['dataset_id'],)).fetchone()
    if not active or active[0] != match['snapshot_id']:
        raise AllocationRejected('STALE_ANALYSIS')
    result = db.execute('SELECT * FROM match_result WHERE match_id=? AND cancel_line_id=?',
                        (match_id, cancel_line_id)).fetchone()
    if (not result or result['classification'] != 'MATCHED_AUTO' or result['original_line_id'] != original_line_id
            or result['requested_quantity'] != quantity or type(quantity) is not int or quantity <= 0
            or producer != 'SYSTEM:'+match['rule_version']):
        raise AllocationRejected('AUTO_ALLOCATION_NOT_AUTHORIZED')
    original = db.execute('SELECT * FROM line_analysis WHERE snapshot_id=? AND line_id=?',
                          (match['snapshot_id'], original_line_id)).fetchone()
    if not original or original['disposition'] != 'RETAINED' or not original['candidate_comparable'] or json.loads(original['quality_tags']):
        raise AllocationRejected('ORIGINAL_NOT_ELIGIBLE')
    existing = db.execute('SELECT allocation_id FROM allocation WHERE dataset_id=? AND cancel_line_id=? AND active=1',
                          (match['dataset_id'], cancel_line_id)).fetchone()
    if existing:
        raise AllocationRejected('CANCELLATION_ALREADY_ALLOCATED')
    balance = available(db, original_line_id, original['quantity'])
    if balance < quantity:
        raise AllocationRejected(f'ALLOCATION_INSUFFICIENT: available={balance}, requested={quantity}')
    aid = digest(['allocation', match['dataset_id'], cancel_line_id, request_key])
    timestamp = now()
    db.execute('INSERT INTO allocation VALUES(?,?,?,?,?,?,?,?,?)',
               (aid, match['dataset_id'], match_id, cancel_line_id, original_line_id, quantity, 1, producer, timestamp))
    db.execute('INSERT INTO ledger_entry VALUES(?,?,?,?,?,?)', (aid, aid, original_line_id, quantity, None, timestamp))
    outcome = {'allocation_id': aid, 'quantity': quantity, 'available_quantity': balance-quantity}
    db.execute('INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?)',
               (digest(['audit', request_key]), 'AUTO_ALLOCATE', aid, producer, request_key,
                compact({'available': balance}), compact(outcome), 'D-38 initial automatic allocation', timestamp))
    db.execute('INSERT INTO operation_receipt VALUES(?,?,?,?)', (request_key, payload, compact(outcome), timestamp))
    return outcome


def allocate_manual_in_transaction(db, *, match_id, cancel_line_id, original_line_id, quantity, producer, request_key):
    """Manual pairing shares the ledger/interlock core; the actor is the producer, not the rule version."""
    if not db.in_transaction:
        raise AllocationRejected('WRITE_TRANSACTION_REQUIRED')
    payload = digest([match_id, cancel_line_id, original_line_id, quantity, producer])
    receipt = db.execute('SELECT * FROM operation_receipt WHERE request_key=?', (request_key,)).fetchone()
    if receipt:
        if receipt['payload_hash'] != payload:
            raise AllocationRejected('IDEMPOTENCY_KEY_CONFLICT')
        return json.loads(receipt['result_json'])
    match = db.execute('SELECT * FROM matching_snapshot WHERE match_id=?', (match_id,)).fetchone()
    if not match or match['status'] not in {'COMMITTING', 'ACTIVE'}:
        raise AllocationRejected('MATCH_VERSION_NOT_ACTIVE')
    active = db.execute('SELECT snapshot_id FROM active_analysis WHERE dataset_id=?', (match['dataset_id'],)).fetchone()
    if not active or active[0] != match['snapshot_id']:
        raise AllocationRejected('STALE_ANALYSIS')
    result = db.execute('SELECT * FROM match_result WHERE match_id=? AND cancel_line_id=?',
                        (match_id, cancel_line_id)).fetchone()
    if (not result or result['requested_quantity'] != quantity or type(quantity) is not int or quantity <= 0
            or not str(producer).startswith('ACTOR:') or len(str(producer)) <= len('ACTOR:')):
        raise AllocationRejected('MANUAL_ALLOCATION_NOT_AUTHORIZED')
    candidate = db.execute('SELECT * FROM candidate WHERE match_id=? AND cancel_line_id=? AND original_line_id=?',
                           (match_id, cancel_line_id, original_line_id)).fetchone()
    if not candidate or json.loads(candidate['quality_json']):
        raise AllocationRejected('CANDIDATE_NOT_ELIGIBLE')
    original = db.execute('SELECT * FROM line_analysis WHERE snapshot_id=? AND line_id=?',
                          (match['snapshot_id'], original_line_id)).fetchone()
    if not original or original['disposition'] != 'RETAINED' or not original['candidate_comparable'] or json.loads(original['quality_tags']):
        raise AllocationRejected('ORIGINAL_NOT_ELIGIBLE')
    existing = db.execute('SELECT allocation_id FROM allocation WHERE dataset_id=? AND cancel_line_id=? AND active=1',
                          (match['dataset_id'], cancel_line_id)).fetchone()
    if existing:
        raise AllocationRejected('CANCELLATION_ALREADY_ALLOCATED')
    balance = available(db, original_line_id, original['quantity'])
    if balance < quantity:
        raise AllocationRejected(f'ALLOCATION_INSUFFICIENT: 可用量已变化 available={balance}, requested={quantity}')
    aid = digest(['allocation', match['dataset_id'], cancel_line_id, request_key])
    timestamp = now()
    db.execute('INSERT INTO allocation VALUES(?,?,?,?,?,?,?,?,?)',
               (aid, match['dataset_id'], match_id, cancel_line_id, original_line_id, quantity, 1, producer, timestamp))
    db.execute('INSERT INTO ledger_entry VALUES(?,?,?,?,?,?)', (aid, aid, original_line_id, quantity, None, timestamp))
    outcome = {'allocation_id': aid, 'quantity': quantity, 'available_quantity': balance-quantity, 'producer': producer}
    db.execute('INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?)',
               (digest(['audit', request_key]), 'MANUAL_ALLOCATE', aid, producer, request_key,
                compact({'available': balance}), compact(outcome), 'Manual pairing with live quota check', timestamp))
    db.execute('INSERT INTO operation_receipt VALUES(?,?,?,?)', (request_key, payload, compact(outcome), timestamp))
    return outcome


def reverse_in_transaction(db, *, allocation_id, producer, request_key, reason=''):
    """Append one reversing ledger entry and deactivate the allocation; a single reversal per entry."""
    if not db.in_transaction:
        raise AllocationRejected('WRITE_TRANSACTION_REQUIRED')
    payload = digest([allocation_id, producer, reason])
    receipt = db.execute('SELECT * FROM operation_receipt WHERE request_key=?', (request_key,)).fetchone()
    if receipt:
        if receipt['payload_hash'] != payload:
            raise AllocationRejected('IDEMPOTENCY_KEY_CONFLICT')
        return json.loads(receipt['result_json'])
    allocation = db.execute('SELECT * FROM allocation WHERE allocation_id=?', (allocation_id,)).fetchone()
    if not allocation:
        raise AllocationRejected('ALLOCATION_NOT_FOUND')
    if not allocation['active']:
        raise AllocationRejected('REVERSAL_ALREADY_EXECUTED')
    entry = db.execute('SELECT * FROM ledger_entry WHERE allocation_id=? AND reverses_entry_id IS NULL',
                       (allocation_id,)).fetchone()
    if not entry:
        raise AllocationRejected('LEDGER_ENTRY_MISSING')
    reversal_id = digest(['reversal', allocation_id, request_key])
    timestamp = now()
    db.execute('INSERT INTO ledger_entry VALUES(?,?,?,?,?,?)',
               (reversal_id, allocation_id, entry['original_line_id'], -entry['delta'], entry['entry_id'], timestamp))
    db.execute('UPDATE allocation SET active=0 WHERE allocation_id=?', (allocation_id,))
    original = db.execute('SELECT quantity FROM line_analysis WHERE snapshot_id=(SELECT snapshot_id FROM matching_snapshot WHERE match_id=?) AND line_id=?',
                          (allocation['match_id'], entry['original_line_id'])).fetchone()
    outcome = {'allocation_id': allocation_id, 'reversal_entry_id': reversal_id,
               'reversed_delta': -entry['delta'],
               'available_quantity': available(db, entry['original_line_id'], original['quantity'])}
    db.execute('INSERT INTO audit_event VALUES(?,?,?,?,?,?,?,?,?)',
               (digest(['audit', request_key]), 'REVERSE_ALLOCATION', allocation_id, producer, request_key,
                compact({'active': 1, 'delta': entry['delta']}), compact(outcome), reason or 'Authorized reversal', timestamp))
    db.execute('INSERT INTO operation_receipt VALUES(?,?,?,?)', (request_key, payload, compact(outcome), timestamp))
    return outcome
