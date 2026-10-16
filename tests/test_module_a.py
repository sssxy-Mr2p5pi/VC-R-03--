"""Module-A business input checks, linked to the frozen TC specifications."""
from decimal import Decimal
import json
import sqlite3
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest

from conftest import line
from vcr03 import cli
from vcr03.ingest.rules import Cell, key
from vcr03.ingest.service import ImportRejected, ingest_workbook
from vcr03.web import create_app


def stored(db):
    with sqlite3.connect(db) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute('SELECT * FROM line_analysis ORDER BY line_id')]


def test_tc01_and_tc11_union_and_duplicate_mapping(workbook,tmp_path):
    negative=line('100','SKU',-2,0,None)
    normal=line('200')
    source=workbook([negative,normal],[normal,negative])
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(source,db)
    s=result['summary']
    assert (s['raw_rows'],s['retained_rows'],s['excluded_rows'])==(4,1,3)
    assert s['exclusion_union']=={'duplicate':2,'negative':2,'overlap':1}
    rows=stored(db)
    assert rows[0]['retained_line_id']==4 and rows[1]['retained_line_id']==3
    assert json.loads(rows[0]['exclusion_tags'])==['CROSS_SHEET_DUPLICATE','NON_C_NEGATIVE_QUANTITY']


def test_tc10_multiset_preserves_internal_duplicates(workbook,tmp_path):
    source=workbook([line(),line()],[line(),line()])
    result=ingest_workbook(source,tmp_path/'db.sqlite')
    assert result['summary']['retained_rows']==2
    assert result['summary']['excluded_rows']==2


def test_tc10_country_difference_keeps_both_versions(workbook,tmp_path):
    source=workbook([line(country='UK')],[line(country='FR')])
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(source,db)
    assert result['summary']['excluded_rows']==0
    assert result['summary']['invoice_version_conflicts']==['100']
    assert all(r['candidate_comparable']==1 for r in stored(db))  # D-38: conflicts remain visible.


def test_tc12_precise_nonproduct_codes_and_positive_cancel(workbook,tmp_path):
    source=workbook([line('1','POST'),line('C2','M',1),line('3','DCGS0003'),line('4','SP1002')])
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(source,db)
    assert result['summary']['nonproduct_rows']==2
    assert [r['line_kind'] for r in stored(db)]==['NONPRODUCT','NONPRODUCT','MERCHANDISE','MERCHANDISE']
    assert stored(db)[1]['primary_reason'] is None


def test_tc13_approved_normalization_and_leading_zero():
    assert key(Cell('\u00a0ａｂＣ\u3000','s'))[:2]==('ABC','ABC')
    assert key(Cell('00012','s'))[0]=='00012'
    assert key(Cell(123.0,'n'))[0]=='123'
    assert key(Cell('123.0','s'))[0]=='123.0'


@pytest.mark.parametrize('character',['\u200b','\u200c','\u200d','\u2060','\ufeff'])
def test_tc13_forbidden_character_detected_before_normalization(character):
    assert key(Cell('A'+character+'B','s'))==(None,None,'KEY_FORBIDDEN_FORMAT_CHAR')


def test_tc13_suspicious_collision_blocks_entire_group(workbook,tmp_path):
    source=workbook([line('1','①'),line('2','1'),line('3','abc'),line('4','ABC')])
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(source,db)
    assert result['summary']['suspicious_key_groups']==1
    rows=stored(db)
    assert all(r['primary_reason']=='KEY_INVALID' and r['candidate_comparable']==0 for r in rows[:2])
    assert all(r['candidate_comparable']==1 for r in rows[2:])


def test_tc14_reason_priority_preserves_all_causes(workbook,tmp_path):
    row=line('C1',None,'text','£2',None)
    row[4]=None
    db=tmp_path/'db.sqlite'
    ingest_workbook(workbook([row]),db)
    result=stored(db)[0]
    assert result['primary_reason']=='MISSING_CUSTOMER'
    assert {r['field'] for r in json.loads(result['quality_tags'])}=={'Customer ID','StockCode','Quantity','Price','InvoiceDate'}
    assert result['quantity'] is None and result['price'] is None


def test_tc17_decimal_text_survives_storage(workbook,tmp_path):
    db=tmp_path/'db.sqlite'
    ingest_workbook(workbook([line(price=0.001)]),db)
    with sqlite3.connect(db) as c:
        price,kind=c.execute('SELECT price,typeof(price) FROM line_analysis').fetchone()
    assert kind=='text' and Decimal(price)==Decimal('0.001')


def test_tc28_reimport_reuses_source_and_snapshot(workbook,tmp_path):
    source=workbook([line()])
    db=tmp_path/'db.sqlite'
    first=ingest_workbook(source,db)
    again=ingest_workbook(source,db)
    assert again['reused'] and first['dataset_id']==again['dataset_id'] and first['snapshot_id']==again['snapshot_id']
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM source_line').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM import_run').fetchone()[0]==2


def test_tc31_rule_change_requires_approval(workbook,tmp_path):
    from vcr03.ingest.dictionary import Dictionary
    source=workbook([line()])
    db=tmp_path/'db.sqlite'
    first=ingest_workbook(source,db)
    changed=Dictionary().content
    changed['version']='synthetic-unapproved-version'
    config=tmp_path/'changed-dictionary.json'
    config.write_text(json.dumps(changed),encoding='utf-8')
    with pytest.raises(ImportRejected,match='RULE_CHANGE_REQUIRES_APPROVAL'):
        ingest_workbook(source,db,dictionary_path=config)
    assert len(stored(db))==1 and stored(db)[0]['snapshot_id']==first['snapshot_id']


def test_tc34_different_hash_cannot_merge(workbook,tmp_path):
    db=tmp_path/'db.sqlite'
    first=ingest_workbook(workbook([line()],name='first.xlsx'),db)
    with pytest.raises(ImportRejected,match='SOURCE_VERSION_REQUIRES_APPROVAL'):
        ingest_workbook(workbook([line('2')],name='second.xlsx'),db)
    assert len(stored(db))==1 and stored(db)[0]['snapshot_id']==first['snapshot_id']


def test_tc35_identity_pending_stays_in_r1(workbook,tmp_path):
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line(None,quantity=-2)]),db)
    assert result['summary']['retained_rows']==1 and result['summary']['excluded_rows']==0
    row=stored(db)[0]
    assert row['side']=='UNKNOWN' and row['reason_category']=='IDENTITY_DATA_QUALITY'


def test_tc35_formula_numeric_text_bool_and_nonproduct_amount(workbook,tmp_path):
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line('C1',quantity='=-2'),line('C2',quantity='-2'),
                                   line('C3',quantity=True),line('C4','M',quantity='bad')]),db)
    rows=stored(db)
    assert all(r['quantity'] is None for r in rows)
    assert json.loads(rows[0]['quality_tags'])[0]['code']=='NUMERIC_FORMULA'
    assert rows[3]['reason_category']=='NONPRODUCT_DATA_QUALITY'
    assert result['summary']['nonproduct'][0]['invalid_amount_rows']==1


def test_tc35_cached_formula_is_still_rejected(workbook,tmp_path):
    source=workbook([line('C1',quantity='=-2')])
    with ZipFile(source) as z:
        parts={name:z.read(name) for name in z.namelist()}
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    xml=ET.fromstring(parts['xl/worksheets/sheet1.xml'])
    cell=xml.find('.//s:c[@r="D2"]',ns)
    assert cell.find('s:f',ns) is not None
    cell.find('s:v',ns).text='-2'
    parts['xl/worksheets/sheet1.xml']=ET.tostring(xml)
    with ZipFile(source,'w') as z:
        for name,data in parts.items():
            z.writestr(name,data)
    db=tmp_path/'db.sqlite'
    ingest_workbook(source,db)
    assert stored(db)[0]['quantity'] is None
    assert any(i['code']=='NUMERIC_FORMULA' for i in json.loads(stored(db)[0]['quality_tags']))


def test_tc13_invoice_collision_becomes_identity_pending(workbook,tmp_path):
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line('①',quantity=-2),line('1')]),db)
    assert result['summary']['retained_rows']==2 and result['summary']['excluded_rows']==0
    assert all(r['side']=='UNKNOWN' and r['reason_category']=='IDENTITY_DATA_QUALITY' for r in stored(db))


def test_tc35_invalid_sale_price_does_not_hide_candidate(workbook,tmp_path):
    db=tmp_path/'db.sqlite'
    ingest_workbook(workbook([line(price=0)]),db)
    row=stored(db)[0]
    assert row['primary_reason']=='PRICE_INVALID_OR_NONPOSITIVE' and row['candidate_comparable']==1


def test_tc37_read_failure_rolls_back_partial_rows(workbook,tmp_path):
    source=workbook([line() for _ in range(1001)])
    db=tmp_path/'db.sqlite'
    with patch('vcr03.ingest.service.parse',side_effect=[__import__('vcr03.ingest.rules',fromlist=['parse']).parse(
        tuple(Cell(v,'d' if hasattr(v,'isoformat') else 's' if isinstance(v,str) else 'n') for v in line()),
        __import__('vcr03.ingest.dictionary',fromlist=['Dictionary']).Dictionary())]*1000+[ValueError('injected read failure')]):
        with pytest.raises(ValueError,match='injected read failure'):
            ingest_workbook(source,db)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM source_line').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM analysis_snapshot').fetchone()[0]==0


def test_tc37_cli_and_page_call_same_service(workbook,tmp_path,monkeypatch,capsys):
    source=workbook([line('C1',quantity=-2),line('1','POST')])
    cli_db,page_db=tmp_path/'cli.sqlite',tmp_path/'web.sqlite'
    monkeypatch.setitem(cli.SOURCES,'full',source)
    with patch('vcr03.ingest.service.ingest_workbook',wraps=ingest_workbook) as shared:
        cli_result=cli.main(['--source','full','--database',str(cli_db)])
        app=create_app({'TESTING':True,'SOURCES':{'full':source},'DATABASES':{'full':page_db}})
        client=app.test_client()
        client.get('/')
        with client.session_transaction() as sess:
            csrf=sess['csrf']
        response=client.post('/import',data={'source':'full','csrf':csrf})
        assert response.status_code==302 and shared.call_count==2
        summary=client.get('/api/summary').json
        assert {k:v for k,v in summary.items() if k!='snapshot_id'}==cli_result['summary']
        assert client.get('/lines?view=quality').status_code==200
        assert client.get('/line/1').status_code==200
        assert client.post('/import',data={'source':'full','csrf':'wrong'}).status_code==400
        assert client.get('/?source=unknown').status_code==400
    capsys.readouterr()


@pytest.mark.parametrize('earlier,later',[(True,1),(1,True)])
def test_tc10_boolean_cannot_be_hidden_by_numeric_duplicate(workbook,tmp_path,earlier,later):
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line(quantity=earlier)],[line(quantity=later)]),db)
    assert result['summary']['exclusion_union']['duplicate']==0
    assert result['summary']['retained_rows']==2
    assert result['summary']['invoice_type_conflicts']==['100']
    rows=stored(db)
    assert sum(r['candidate_comparable'] for r in rows)==1  # Only the valid numeric quantity is searchable.
    damaged=rows[0 if earlier is True else 1]
    assert damaged['quantity'] is None
    assert any(i['code']=='NUMERIC_TYPE_INVALID' for i in json.loads(damaged['quality_tags']))


@pytest.mark.parametrize('value',['=2','#VALUE!'])
def test_tc10_cross_sheet_formula_error_never_deduplicated(workbook,tmp_path,value):
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line(quantity=value)],[line(quantity=value)]),db)
    assert result['summary']['retained_rows']==2 and result['summary']['excluded_rows']==0
    assert result['summary']['invoice_type_conflicts']==['100']
    assert all(any(i['code']=='INVOICE_DEDUP_TYPE_CONFLICT' for i in json.loads(r['quality_tags'])) for r in stored(db))


def test_tc10_customer_stock_approved_key_exception(workbook,tmp_path):
    first=line(stock=85123,customer=123)
    later=line(stock='８５１２３',customer=' 123 ')
    result=ingest_workbook(workbook([first],[later]),tmp_path/'db.sqlite')
    assert result['summary']['retained_rows']==1 and result['summary']['excluded_rows']==1
    assert result['summary']['invoice_type_conflicts']==[]


def test_tc10_invalid_key_falls_back_to_raw_type(workbook,tmp_path):
    # Missing keys retain their typed evidence; a missing numeric cell and empty text differ.
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line(customer=None)],[line(customer='')]),db)
    assert result['summary']['retained_rows']==2
    assert result['summary']['invoice_type_conflicts']==['100']
    assert all(r['primary_reason']=='MISSING_CUSTOMER' for r in stored(db))


def test_tc10_suspicious_key_not_merged_even_if_nfkc_key_equal(workbook,tmp_path):
    result=ingest_workbook(workbook([line(stock='①')],[line(stock='1')]),tmp_path/'db.sqlite')
    assert result['summary']['suspicious_key_groups']==1
    assert result['summary']['retained_rows']==2 and result['summary']['excluded_rows']==0


@pytest.mark.parametrize('later,excluded',[(21494,1),('21494',0)])
def test_tc10_description_retains_original_type(workbook,tmp_path,later,excluded):
    first=line()
    first[2]=21494
    second=line()
    second[2]=later
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([first],[second]),db)
    assert result['summary']['excluded_rows']==excluded
    if excluded:
        assert stored(db)[1]['primary_reason'] is None
    else:
        assert result['summary']['invoice_type_conflicts']==['100']


def test_tc35_text_formula_field_priority_and_candidate_visibility(workbook,tmp_path):
    rows=[line('C1',quantity=-2),line('C2',quantity='=-2',customer=None),
          line('3'),line('C4','M',quantity=1)]
    for row in rows:
        row[2]='="fixture"'
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook(rows),db)
    actual=stored(db)
    assert [r['primary_reason'] for r in actual]==['TEXT_FIELD_FORMULA_OR_ERROR','MISSING_CUSTOMER',
                                                'TEXT_FIELD_FORMULA_OR_ERROR','NP_DATA_QUALITY']
    assert actual[2]['candidate_comparable']==1 and actual[2]['reason_category']=='DATA_QUALITY'
    assert actual[3]['reason_category']=='NONPRODUCT_DATA_QUALITY'
    assert result['summary']['invoice_version_conflicts']==[]
    assert {i['code'] for i in json.loads(actual[1]['quality_tags'])}=={
        'KEY_MISSING','NUMERIC_FORMULA','TEXT_FIELD_FORMULA_OR_ERROR'}


@pytest.mark.parametrize('invoice,qty,expected',[
    ('C1',0,'QUANTITY_INVALID_OR_SIGN'),('C1',2,'QUANTITY_INVALID_OR_SIGN'),
    ('1',0,'QUANTITY_INVALID_OR_SIGN'),('1',0.5,'QUANTITY_INVALID_OR_SIGN')])
def test_tc35_quantity_zero_sign_fractional_invalid(workbook,tmp_path,invoice,qty,expected):
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line(invoice,quantity=qty)]),db)
    assert result['summary']['retained_rows']==1
    assert stored(db)[0]['primary_reason']==expected and stored(db)[0]['candidate_comparable']==0


def test_tc14_bad_time_precedes_quantity_and_price(workbook,tmp_path):
    row=line('C1',quantity='bad',price='bad')
    row[4]='2020-01-01'
    db=tmp_path/'db.sqlite'
    ingest_workbook(workbook([row]),db)
    assert stored(db)[0]['primary_reason']=='TIME_MISSING_OR_INVALID'
    assert len(json.loads(stored(db)[0]['quality_tags']))==3


def test_tc13_sharp_s_suspicious_collision(workbook,tmp_path):
    db=tmp_path/'db.sqlite'
    result=ingest_workbook(workbook([line('1','ß'),line('2','SS')]),db)
    assert result['summary']['suspicious_key_groups']==1
    assert all(r['candidate_comparable']==0 for r in stored(db))


def test_tc37_comparison_finishes_before_business_validation(workbook,tmp_path):
    from vcr03.ingest import service
    events=[]
    original_compare,original_parse=service.typed_content,service.parse
    def compare(*args,**kwargs):
        events.append('compare')
        return original_compare(*args,**kwargs)
    def validate(*args,**kwargs):
        events.append('validate')
        return original_parse(*args,**kwargs)
    with patch.object(service,'typed_content',side_effect=compare),patch.object(service,'parse',side_effect=validate):
        ingest_workbook(workbook([line(quantity=True)],[line(quantity=1)]),tmp_path/'db.sqlite')
    assert events==['compare','compare','validate','validate']


def test_tc31_approved_unchanged_refresh_keeps_source_history_and_activates(workbook,tmp_path):
    from vcr03.ingest.dictionary import Dictionary
    source=workbook([line()])
    db=tmp_path/'db.sqlite'
    first=ingest_workbook(source,db)
    config=Dictionary().content
    config['version']='synthetic-approved-version'
    path=tmp_path/'approved.json'
    path.write_text(json.dumps(config),encoding='utf-8')
    again=ingest_workbook(source,db,dictionary_path=path,approved_unchanged_rule_refresh=True)
    assert again['dataset_id']==first['dataset_id'] and again['snapshot_id']!=first['snapshot_id']
    assert again['summary']['approved_refresh']['changed_output_rows']==0
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM source_line').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM analysis_snapshot').fetchone()[0]==2
        assert c.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0]==again['snapshot_id']
    client=create_app({'TESTING':True,'SOURCES':{'full':source},'DATABASES':{'full':db}}).test_client()
    assert client.get('/api/summary').json['snapshot_id']==again['snapshot_id']


def test_tc31_changed_output_refresh_rolls_back(workbook,tmp_path):
    from vcr03.ingest.dictionary import Dictionary
    source=workbook([line(stock='SKU')])
    db=tmp_path/'db.sqlite'
    first=ingest_workbook(source,db)
    config=Dictionary().content
    config['version']='synthetic-changes-results'
    config['entries'].append({'code':'SKU','name':'synthetic','category':'synthetic'})
    path=tmp_path/'changes.json'
    path.write_text(json.dumps(config),encoding='utf-8')
    with pytest.raises(ImportRejected,match='RULE_RESULTS_CHANGED_REQUIRES_CONFIRMATION'):
        ingest_workbook(source,db,dictionary_path=path,approved_unchanged_rule_refresh=True)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM analysis_snapshot').fetchone()[0]==1
        assert c.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0]==first['snapshot_id']
