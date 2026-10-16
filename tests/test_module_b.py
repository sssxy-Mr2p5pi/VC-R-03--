"""Synthetic fixtures for B/initial-ticket contracts; never course transaction data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import threading

import pytest
from conftest import line
from vcr03.ingest.service import ingest_workbook
from vcr03.matching import query, rules, service
from vcr03.storage.database import connect
from vcr03.web import create_app
from vcr03.workflow.allocation import AllocationRejected, allocate_in_transaction


def run(workbook,tmp_path,rows,second=()):
    source = workbook(rows,second)
    db = tmp_path/'b.sqlite'
    result = service.reconcile_workbook(source,db)
    return db,result


def stored(db,table):
    with closing(connect(db)) as connection:
        return [dict(r) for r in connection.execute('SELECT * FROM '+table)]


def test_tc02_16_partial_return_and_actual_allocation(workbook,tmp_path):
    db,result = run(workbook,tmp_path,[line(quantity=10),line('C1',quantity=-4)])
    r = stored(db,'match_result')[0]
    assert r['classification']=='MATCHED_AUTO' and r['candidate_count']==1
    assert stored(db,'allocation')[0]['quantity']==4
    assert stored(db,'ledger_entry')[0]['delta']==4
    assert not stored(db,'ticket')
    d = query.detail(db,r['cancel_line_id'])
    assert d['candidates'][0]['available_quantity']==6
    assert result['matching']['summary']['balances'] and all(c['valid'] for c in result['matching']['summary']['balances'])


@pytest.mark.parametrize('offset,reason',[(1,'ONLY_LATER_SALES'),(0,None),(-1,None)])
def test_tc15_time_boundary(workbook,tmp_path,offset,reason):
    sale = line(quantity=10)
    sale[4] += timedelta(seconds=offset)
    db,_ = run(workbook,tmp_path,[sale,line('C1',quantity=-1)])
    r = stored(db,'match_result')[0]
    assert r['primary_reason']==reason


def test_tc02_no_same_customer_or_stock(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line(customer=999),line('C1',quantity=-1)])
    assert stored(db,'match_result')[0]['primary_reason']=='NO_SAME_CUSTOMER_STOCK_IN_DATASET'


def test_tc18_all_unique_demand_includes_price_failure(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line(quantity=10),line('C1',quantity=-6),line('C2',quantity=-6,price=3)])
    rows = stored(db,'match_result')
    assert [r['primary_reason'] for r in rows]==['CUMULATIVE_OVER_QUOTA','PRICE_DIFFERENCE']
    assert all('CUMULATIVE_OVER_QUOTA' in r['reason_tags'] for r in rows)
    assert stored(db,'quota_demand')[0]['requested_quantity']==12
    assert not stored(db,'allocation')


def test_tc18_input_order_invariant(workbook,tmp_path):
    source = workbook([line(quantity=10),line('C1',quantity=-6),line('C2',quantity=-6,price=3)])
    db = tmp_path/'a.sqlite'
    a = ingest_workbook(source,db)
    with closing(connect(db)) as c:
        sales,cancel = service.load_rows(c,a['snapshot_id'])
    assert rules.analyze(sales,cancel)==rules.analyze(list(reversed(sales)),list(reversed(cancel)))


def test_tc17_decimal_zero_tolerance_and_quantity_reasons(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line(quantity=1,price=0.001),line('C1',quantity=-2,price=0.002)])
    r = stored(db,'match_result')[0]
    assert Decimal(r['price_delta'])==Decimal('0.001')
    assert {t['code'] for t in json.loads(r['reason_tags'])}=={'PRICE_DIFFERENCE','SINGLE_RETURN_EXCEEDS_ORIGINAL','CUMULATIVE_OVER_QUOTA'}
    assert not stored(db,'allocation')


def test_tc19_multiple_and_combination_no_allocation(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line('1',quantity=3),line('2',quantity=3),line('C1',quantity=-5)])
    r = stored(db,'match_result')[0]
    assert r['candidate_count']==2 and r['primary_reason']=='MULTIPLE_CANDIDATES'
    assert 'COMBINATION_POSSIBLE_NOT_PROVEN' in r['reason_tags']
    assert not stored(db,'ledger_entry')


@pytest.mark.parametrize('field,value,reason',[(5,0,'PRICE_INVALID_OR_NONPOSITIVE'),(5,'bad','PRICE_INVALID_OR_NONPOSITIVE'),(2,'="x"','TEXT_FIELD_FORMULA_OR_ERROR')])
def test_tc14_35_unique_candidate_quality_propagates(workbook,tmp_path,field,value,reason):
    sale = line(quantity=10)
    sale[field] = value
    db,result = run(workbook,tmp_path,[sale,line('C1',quantity=-1)])
    r = stored(db,'match_result')[0]
    assert r['candidate_count']==1 and r['primary_reason']==reason and r['classification']=='SUSPECTED'
    assert r['candidate_quality']==1 and not r['own_quality']
    assert any(t['origin_line_id']==1 and t['category']=='DATA_QUALITY' for t in json.loads(r['reason_tags']))
    assert result['matching']['summary']['cancellations_affected_by_candidate_quality']==1
    assert not stored(db,'allocation')


def test_tc10_19_conflict_cannot_create_false_unique(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line('1',quantity=10),line('2',quantity=10),line('C1',quantity=-1)],
               [line('1',quantity=10,country='FR')])
    r = stored(db,'match_result')[0]
    assert r['candidate_count']==3 and r['primary_reason']=='MULTIPLE_CANDIDATES'
    assert stored(db,'ticket')[0]['ticket_type']=='MIXED'
    assert len(stored(db,'candidate'))==3 and not stored(db,'allocation')


def test_tc14_35_cancel_quality_null_candidates_and_identity(workbook,tmp_path):
    bad = line('C1',stock=None,quantity='bad',customer=None)
    db,_ = run(workbook,tmp_path,[bad,line(invoice=None)])
    r = stored(db,'match_result')[0]
    assert r['classification']=='NO_ORIGINAL' and r['primary_reason']=='MISSING_CUSTOMER'
    assert r['candidate_count'] is None and r['price_delta'] is None and not r['candidate_computed']
    assert len(stored(db,'ticket'))==1 and len(stored(db,'source_line'))==2


def test_tc03_21_three_ticket_types_and_normal_lines(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line(quantity=10),line('C1',quantity=-1),line('C1','MISSING',-1),
                                line('C2',quantity=-1,customer=None),line('C3','X',-1),line('C3',quantity=-1,customer=None),
                                line('C1','POST',1,1),line('C4','POST',1,2)])
    types = {r['invoice_key']:r['ticket_type'] for r in stored(db,'ticket')}
    assert types=={'C1':'MATCHING_DISCREPANCY','C2':'DATA_QUALITY','C3':'MIXED'}
    assert len(stored(db,'ticket_line'))==5 and len(stored(db,'allocation'))==1
    assert all(t['status']=='PENDING' and t['assignee'] is None for t in stored(db,'ticket'))
    c1 = query.invoice_detail(db,'C1')
    assert len(c1['lines'])==2 and len(c1['nonproduct'])==1
    assert Decimal(c1['amounts']['total_amount'])==Decimal('-4.10')
    assert query.invoice_detail(db,'C4')['ticket'] is None


def test_tc21_invalid_nonproduct_amount_is_incomplete(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line('C1','POST',1,'bad')])
    item = query.invoice_detail(db,'C1')
    assert item['amounts']['total_amount'] is None and not item['amount_complete']
    assert not stored(db,'ticket')


def test_tc28_29_repeated_pipeline_preserves_business_state(workbook,tmp_path):
    source = workbook([line(quantity=10),line('C1',quantity=-1),line('C2','NONE',-1)])
    db = tmp_path/'b.sqlite'
    first = service.reconcile_workbook(source,db)
    counts = {t:len(stored(db,t)) for t in ['match_result','candidate','ticket','allocation','ledger_entry','audit_event','operation_receipt']}
    # Synthetic fixture representing a later C workflow state, not a production C endpoint.
    with closing(connect(db)) as c:
        c.execute("UPDATE ticket SET status='CLOSED',current_round=2,version=9")
        c.commit()
    repeated = service.reconcile_workbook(source,db)
    assert first['matching']['match_id']==repeated['matching']['match_id'] and repeated['matching']['reused']
    assert counts=={t:len(stored(db,t)) for t in counts}
    assert stored(db,'ticket')[0]['status']=='CLOSED' and stored(db,'ticket')[0]['version']==9


def test_tc31_new_matching_version_preview_only(workbook,tmp_path):
    db,_ = run(workbook,tmp_path,[line(),line('C1',quantity=-1),line('C2','NO',-1)])
    active = stored(db,'active_matching')
    allocations,tickets = stored(db,'allocation'),stored(db,'ticket')
    preview = service.match_snapshot(db,rule_version='synthetic-new-version')
    assert preview['status']=='REQUIRES_REVIEW'
    assert stored(db,'active_matching')==active and stored(db,'allocation')==allocations and stored(db,'ticket')==tickets
    assert service.match_snapshot(db,rule_version='synthetic-new-version')['reused']


def test_tc29_failure_rolls_back_all_b_but_keeps_a(workbook,tmp_path,monkeypatch):
    source = workbook([line(quantity=10),line('C1',quantity=-1),line('C2',quantity=-1)])
    db = tmp_path/'b.sqlite'
    allocate = service.allocate_in_transaction
    calls = []
    def fail(c,**kwargs):
        result = allocate(c,**kwargs)
        calls.append(result)
        if len(calls)==2:
            raise RuntimeError('synthetic transaction failure')
        return result
    monkeypatch.setattr(service,'allocate_in_transaction',fail)
    with pytest.raises(RuntimeError,match='synthetic'):
        service.reconcile_workbook(source,db)
    assert len(stored(db,'source_line'))==3
    for table in ['matching_snapshot','match_result','candidate','allocation','ledger_entry','ticket','operation_receipt','audit_event']:
        assert not stored(db,table)
    monkeypatch.setattr(service,'allocate_in_transaction',allocate)
    assert service.reconcile_workbook(source,db)['matching']['status']=='ACTIVE'


def test_tc30_concurrent_initial_runs_allocate_once(workbook,tmp_path,monkeypatch):
    source = workbook([line(quantity=10),line('C1',quantity=-4)])
    db = tmp_path/'b.sqlite'
    ingest_workbook(source,db)
    barrier = threading.Barrier(2)
    original = service.analyze
    def synchronized(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(service,'analyze',synchronized)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(service.match_snapshot,db) for _ in range(2)]
        outcomes = [f.result() for f in futures]
    assert sorted(r['reused'] for r in outcomes)==[False,True]
    assert len(stored(db,'allocation'))==1 and len(stored(db,'ledger_entry'))==1


def test_tc32_stale_analysis_rejected(workbook,tmp_path,monkeypatch):
    source = workbook([line(quantity=10),line('C1',quantity=-1)])
    db = tmp_path/'b.sqlite'
    ingest_workbook(source,db)
    original = service.analyze
    def changed(*args):
        result = original(*args)
        with closing(connect(db)) as c:
            c.execute('DELETE FROM active_analysis')
            c.commit()
        return result
    monkeypatch.setattr(service,'analyze',changed)
    with pytest.raises(service.ReconciliationRejected,match='STALE_ANALYSIS'):
        service.match_snapshot(db)
    assert not stored(db,'allocation')


def test_tc29_kernel_replay_payload_and_transaction_guard(workbook,tmp_path):
    db,ran = run(workbook,tmp_path,[line(quantity=10),line('C1',quantity=-4)])
    mid = ran['matching']['match_id']
    args = dict(match_id=mid,cancel_line_id=2,original_line_id=1,quantity=4,
                producer='SYSTEM:'+rules.MATCH_VERSION,request_key=f'auto:{mid}:2')
    with closing(connect(db)) as c:
        with pytest.raises(AllocationRejected,match='TRANSACTION'):
            allocate_in_transaction(c,**args)
        c.execute('BEGIN IMMEDIATE')
        receipt = allocate_in_transaction(c,**args)
        assert receipt['quantity']==4
        with pytest.raises(AllocationRejected,match='IDEMPOTENCY_KEY_CONFLICT'):
            allocate_in_transaction(c,**{**args,'quantity':5})
        with pytest.raises(AllocationRejected,match='ALREADY_ALLOCATED'):
            allocate_in_transaction(c,**{**args,'request_key':'another-request'})
        c.rollback()
    assert len(stored(db,'ledger_entry'))==1


def test_tc32_ledger_rechecked_inside_transaction(workbook,tmp_path,monkeypatch):
    source = workbook([line(quantity=10),line('C1',quantity=-4),line('C2',quantity=-4)])
    db = tmp_path/'b.sqlite'
    original = service.allocate_in_transaction
    calls = []
    def simulate_changed_balance(c,**kwargs):
        if calls:
            # Synthetic existing downstream occupation: exercise balance recheck and rollback.
            c.execute('UPDATE ledger_entry SET delta=8')
        calls.append(1)
        return original(c,**kwargs)
    monkeypatch.setattr(service,'allocate_in_transaction',simulate_changed_balance)
    with pytest.raises(AllocationRejected,match='ALLOCATION_INSUFFICIENT'):
        service.reconcile_workbook(source,db)
    assert not stored(db,'allocation') and not stored(db,'ledger_entry')


def test_tc20_37_web_cli_shared_service_and_exports(workbook,tmp_path,monkeypatch):
    source = workbook([line(quantity=10),line('C1',quantity=-4),line('C2','NONE',-1)])
    db = tmp_path/'b.sqlite'
    app = create_app({'TESTING':True,'SOURCES':{'full':source},'DATABASES':{'full':db}})
    calls = []
    original = service.reconcile_workbook
    def called(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(service,'reconcile_workbook',called)
    client = app.test_client()
    client.get('/')
    with client.session_transaction() as sess:
        csrf = sess['csrf']
    assert client.post('/reconcile',data={'csrf':csrf}).status_code==302
    script = Path(__file__).resolve().parents[1]/'scripts/reconcile_data.py'
    spec = importlib.util.spec_from_file_location('test_reconcile_cli',script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(module.SOURCES,'full',source)
    module.main(['--source','full','--database',str(db)])
    assert len(calls)==2
    for url in ['/matches','/matches/2','/tickets','/invoices/C1','/invoices/C2','/api/matching-summary',
                '/exports/results.csv','/exports/candidates.csv','/exports/tickets.csv']:
        response = client.get(url,buffered=True)
        assert response.status_code==200, (url,response.data)
    assert 'source_hash' in client.get('/exports/results.csv').text
    assert 'available_quantity' in client.get('/exports/candidates.csv').text
    assert 'history' in client.get('/exports/tickets.csv').text
    assert client.post('/reconcile',data={'csrf':'bad'}).status_code==400
    assert client.get('/matches?page=bad').status_code==400
    assert client.get('/matches?source=invalid').status_code==400
    assert client.get('/matches/999').status_code==404


def test_tc19_query_keeps_exhausted_candidates(workbook,tmp_path):
    first = line('1',quantity=2)
    later = line('2',quantity=2)
    later[4] += timedelta(days=1)
    cancel = line('C2',quantity=-1)
    cancel[4] = datetime(2020,1,3)
    db,_ = run(workbook,tmp_path,[first,line('C1',quantity=-2),later,cancel])
    detail = query.detail(db,4)
    assert len(detail['candidates'])==2
    assert [c['available_quantity'] for c in detail['candidates']]==[0,2]
    assert detail['classification']=='SUSPECTED'
