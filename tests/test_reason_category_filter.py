"""CF-01..CF-06 reason-category filtering over the real synthetic pipeline (D-39 batch)."""
from collections import defaultdict
from contextlib import closing
import csv
import io
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import line
from vcr03.matching import query, service
from vcr03.storage.database import connect
from vcr03.web import create_app

DUAL = {'DATA_QUALITY', 'MATCHING_DISCREPANCY'}


def pipeline(workbook, tmp_path):
    """Real import+matching pipeline: single-, dual- and multi-candidate reason rows.

    Line ids are positional: every cancel recorded in ``expected`` is the row appended
    right after its sale(s), so line id == position in the first sheet.
    """
    first, expected = [], {}

    def sale(invoice, stock, description='Synthetic test fixture'):
        first.append(line(invoice, stock, quantity=10, price=2.55))
        first[-1][2] = description

    def cancel(invoice, stock, price=2.55, categories=()):
        first.append(line(invoice, stock, quantity=-2, price=price))
        expected[len(first)] = set(categories)

    sale('101', 'A1', description='="x"')  # candidate-propagated quality only
    cancel('C1', 'A1', categories=['DATA_QUALITY'])
    sale('102', 'B1')  # clean candidate, price difference only
    cancel('C2', 'B1', price=3, categories=['MATCHING_DISCREPANCY'])
    sale('103', 'D1', description='="x"')  # both categories on one row
    cancel('C3', 'D1', price=3, categories=DUAL)
    sale('104', 'E1')  # two clean candidates for the candidates export
    sale('105', 'E1')
    cancel('C4', 'E1', categories=['MATCHING_DISCREPANCY'])
    sale('106', 'G1', description='="x"')  # multi-candidate primary + propagated quality
    sale('107', 'G1')
    cancel('C5', 'G1', categories=DUAL)
    for i in range(52):  # pads MATCHING_DISCREPANCY past one 50-row page
        sale(f'2{i:02d}', f'F{i}')
        cancel(f'C{i+10:02d}', f'F{i}', price=3, categories=['MATCHING_DISCREPANCY'])
    source = workbook(first)
    db = tmp_path / 'reason-category.sqlite'
    service.reconcile_workbook(source, db)
    return source, db, expected


def expected_ids(expected, category=''):
    return {lid for lid, categories in expected.items() if not category or category in categories}


def client_for(source, db):
    app = create_app({'TESTING': True, 'SOURCES': {'full': source}, 'DATABASES': {'full': db}})
    return app.test_client()


def rendered_links(html):
    return [raw.replace('&amp;', '&') for raw in re.findall(r'href="([^"]+)"', html)]


def csv_rows(response):
    text = response.get_data(as_text=True)
    assert text.startswith('\ufeff')
    return [dict(row) for row in csv.DictReader(io.StringIO(text[1:]))]


def test_cf01_single_dual_and_no_category_queries(workbook, tmp_path):
    """CF-01（TC-22）：单类别、双类别、无类别/无命中；双类别分别可查、一次结果不重复。"""
    _, db, expected = pipeline(workbook, tmp_path)
    assert len(expected) == 57
    with closing(connect(db)) as connection:
        stored = {r['cancel_line_id']: set(json.loads(r['reason_categories']))
                  for r in connection.execute('SELECT cancel_line_id,reason_categories FROM match_result')}
    assert stored == expected  # dual rows really carry both categories
    assert sum(categories == DUAL for categories in expected.values()) == 2

    all_rows = [r['cancel_line_id'] for r in query.results(db, limit=1000)['rows']]
    assert set(all_rows) == set(expected) and len(all_rows) == len(set(all_rows))
    quality = query.results(db, category='DATA_QUALITY', limit=1000)
    matching = query.results(db, category='MATCHING_DISCREPANCY', limit=1000)
    assert quality['total'] == 3 and matching['total'] == 56
    assert {r['cancel_line_id'] for r in quality['rows']} == expected_ids(expected, 'DATA_QUALITY')
    assert {r['cancel_line_id'] for r in matching['rows']} == expected_ids(expected, 'MATCHING_DISCREPANCY')
    for data in (quality, matching):  # no duplicate rows in one filtered pass
        ids = [r['cancel_line_id'] for r in data['rows']]
        assert len(ids) == len(set(ids))
    dual = expected_ids(expected, 'DATA_QUALITY') & expected_ids(expected, 'MATCHING_DISCREPANCY')
    assert len(dual) == 2 and all(lid in {r['cancel_line_id'] for r in quality['rows']} for lid in dual)
    no_hit = query.results(db, category='DATA_QUALITY', classification='MATCHED_AUTO', limit=1000)
    assert no_hit['total'] == 0 and no_hit['rows'] == [] and no_hit['pages'] == 1


def test_cf02_multi_candidate_primary_and_propagated_quality(workbook, tmp_path):
    """CF-02（TC-14/22）：多候选主因保持，候选传播的质量原因仍使该取消行命中DATA_QUALITY。"""
    _, db, _ = pipeline(workbook, tmp_path)
    with closing(connect(db)) as connection:
        row = connection.execute("SELECT * FROM match_result WHERE primary_reason='MULTIPLE_CANDIDATES' "
                                 'AND candidate_quality=1').fetchone()
    assert row is not None and row['candidate_count'] == 2
    assert json.loads(row['reason_categories']) == ['DATA_QUALITY', 'MATCHING_DISCREPANCY']
    hit = {r['cancel_line_id'] for r in query.results(db, category='DATA_QUALITY', limit=1000)['rows']}
    assert row['cancel_line_id'] in hit
    detail = query.detail(db, row['cancel_line_id'])
    assert detail['primary_reason'] == 'MULTIPLE_CANDIDATES'
    assert len(detail['candidates']) == 2 and any(candidate['quality'] for candidate in detail['candidates'])


def test_cf03_and_combination_empty_category_and_paging_totals(workbook, tmp_path):
    """CF-03（TC-20/22）：类别与分类、主因AND；空类别保留原结果；过滤总数与分页一致。"""
    _, db, expected = pipeline(workbook, tmp_path)
    original = query.results(db, limit=1000)
    empty = query.results(db, category='', limit=1000)
    assert empty['total'] == original['total'] == len(expected)
    assert [r['cancel_line_id'] for r in empty['rows']] == [r['cancel_line_id'] for r in original['rows']]

    by_category = query.results(db, category='MATCHING_DISCREPANCY', limit=1000)
    by_reason = query.results(db, reason='PRICE_DIFFERENCE', limit=1000)
    combined = query.results(db, category='MATCHING_DISCREPANCY', reason='PRICE_DIFFERENCE', limit=1000)
    category_ids = {r['cancel_line_id'] for r in by_category['rows']}
    reason_ids = {r['cancel_line_id'] for r in by_reason['rows']}
    assert {r['cancel_line_id'] for r in combined['rows']} == category_ids & reason_ids
    assert combined['total'] == len(combined['rows']) == len(category_ids & reason_ids) == 53
    suspended = query.results(db, category='MATCHING_DISCREPANCY', classification='SUSPECTED', limit=1000)
    assert suspended['total'] == by_category['total'] == 56
    no_hit = query.results(db, category='MATCHING_DISCREPANCY', classification='MATCHED_AUTO', limit=1000)
    assert no_hit['total'] == 0 and no_hit['rows'] == []

    page1 = query.results(db, category='MATCHING_DISCREPANCY')
    page2 = query.results(db, category='MATCHING_DISCREPANCY', page=2)
    assert page1['total'] == page2['total'] == 56 and page1['pages'] == 2
    assert len(page1['rows']) == 50 and len(page2['rows']) == 6
    paged = {r['cancel_line_id'] for r in page1['rows']} | {r['cancel_line_id'] for r in page2['rows']}
    assert paged == expected_ids(expected, 'MATCHING_DISCREPANCY')


def test_cf04_page_selection_and_links_keep_category(workbook, tmp_path):
    """CF-04（TC-20/37）：页面选择、翻页与导出链接保留类别和source等已有参数。"""
    source, db, _ = pipeline(workbook, tmp_path)
    client = client_for(source, db)
    html = client.get('/matches?category=MATCHING_DISCREPANCY&classification=SUSPECTED').get_data(as_text=True)
    assert '<option value="MATCHING_DISCREPANCY" selected>' in html
    links = rendered_links(html)
    next_links = [link for link in links if link.startswith('/matches?') and 'page=2' in link]
    assert len(next_links) == 1
    params = parse_qs(urlsplit(next_links[0]).query)
    assert params['category'] == ['MATCHING_DISCREPANCY'] and params['classification'] == ['SUSPECTED']
    assert params['source'] == ['full']
    page2 = client.get('/matches?category=MATCHING_DISCREPANCY&classification=SUSPECTED&page=2').get_data(as_text=True)
    previous = [link for link in rendered_links(page2) if link.startswith('/matches?') and 'page=1' in link]
    assert len(previous) == 1
    assert parse_qs(urlsplit(previous[0]).query)['category'] == ['MATCHING_DISCREPANCY']
    exports = [link for link in links if link.startswith('/exports/')]
    assert {urlsplit(link).path for link in exports} == {'/exports/results.csv', '/exports/candidates.csv'}
    for link in exports:
        params = parse_qs(urlsplit(link).query)
        assert params['category'] == ['MATCHING_DISCREPANCY'] and params['classification'] == ['SUSPECTED']
        assert params['source'] == ['full']


def test_cf05_export_sets_and_full_candidates(workbook, tmp_path):
    """CF-05（TC-20/22）：结果CSV与未分页筛选结果集合相同；候选CSV保留筛中取消行的全部候选；完整原因保持。"""
    source, db, expected = pipeline(workbook, tmp_path)
    client = client_for(source, db)
    for category in ('', 'DATA_QUALITY', 'MATCHING_DISCREPANCY'):
        response = client.get(f'/exports/results.csv?category={category}', buffered=True)
        assert response.status_code == 200
        exported = {int(row['cancel_line_id']) for row in csv_rows(response)}
        queried = {row['cancel_line_id'] for row in query.results(db, category=category, limit=1000)['rows']}
        assert exported == queried == expected_ids(expected, category)
    dual = min(lid for lid, categories in expected.items() if categories == DUAL)
    response = client.get('/exports/results.csv?category=DATA_QUALITY', buffered=True)
    row = next(r for r in csv_rows(response) if int(r['cancel_line_id']) == dual)
    assert set(json.loads(row['reason_categories'])) == DUAL
    assert {tag['category'] for tag in json.loads(row['reason_tags'])} == DUAL
    response = client.get('/exports/candidates.csv?category=MATCHING_DISCREPANCY', buffered=True)
    assert response.status_code == 200
    by_cancel = defaultdict(set)
    for r in csv_rows(response):
        by_cancel[int(r['cancel_line_id'])].add(int(r['original_line_id']))
    assert set(by_cancel) == expected_ids(expected, 'MATCHING_DISCREPANCY')
    assert by_cancel[9] == {7, 8}  # both clean candidates of the multi-candidate cancel
    assert by_cancel[12] == {10, 11}  # candidates not filtered even when one carries quality


def test_cf06_unknown_category_rejected_before_csv_bytes(workbook, tmp_path):
    """CF-06（TC-37）：未知类别在页面和CSV响应开始前返回400，不输出半份CSV或静默取消筛选。"""
    source, db, _ = pipeline(workbook, tmp_path)
    client = client_for(source, db)
    for url in ('/matches?category=UNKNOWN', '/matches?category=DATA_QUALITY_EXTRA',
                '/matches?category=matching_discrepancy'):
        assert client.get(url).status_code == 400
    with pytest.raises(ValueError, match='INVALID_REASON_CATEGORY'):
        query.results(db, category='UNKNOWN')
    with pytest.raises(ValueError, match='INVALID_REASON_CATEGORY'):
        list(query.export_rows(db, 'results', category='UNKNOWN'))
    for kind in ('results', 'candidates', 'tickets'):
        response = client.get(f'/exports/{kind}.csv?category=UNKNOWN', buffered=True)
        assert response.status_code == 400
        assert response.mimetype != 'text/csv'
        body = response.get_data()
        assert not body.startswith(b'\xef\xbb\xbf') and b'cancel_line_id' not in body and b'source_hash' not in body
