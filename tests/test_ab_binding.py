"""D-39 A/B 绑定保护验收：AB-01～AB-05，全部使用合成夹具，不写真实业务库。

对照 evidence/plans/2026-10-16-ab-filter/验收方案.md：
- AB-01（TC-31）：先A→有效B，再批准零输出变化A规则刷新；目标快照不同，激活拒绝。
- AB-02（TC-30）：失败刷新全回滚；来源/分析/匹配快照与当前指针、配对、额度、工单/轮次、
  已存在回执等既有记录保持。
- AB-03（TC-28/29）：同快照重导/重试继续返回正确幂等结果，无重复占量/建单。
- AB-04（TC-31）：无B表或无有效B（含待核B）时，原已批准的无变化刷新仍可用。
- AB-05（TC-31/32）：各激活分支均受保护；用确定性两连接反例核对检查与激活处于同一写事务。
"""
import json
import sqlite3
from contextlib import closing

import pytest

from conftest import line
from vcr03.ingest import service as ingest_service
from vcr03.ingest.dictionary import Dictionary
from vcr03.ingest.service import ImportRejected, ingest_workbook
from vcr03.matching import service as matching_service
from vcr03.matching.storage import initialize as initialize_matching
from vcr03.storage.database import connect

CONFLICT_CODE = 'A_BINDING_CONFLICT_REQUIRES_APPROVAL'
AB02_TABLES = ('source_dataset', 'analysis_snapshot', 'active_analysis', 'source_line', 'line_analysis',
               'import_run', 'matching_snapshot', 'active_matching', 'allocation', 'ledger_entry', 'ticket',
               'ticket_line', 'processing_round', 'operation_receipt')
ROWS = [line(quantity=10), line('C1', quantity=-4), line('C2', quantity=-1, customer=999)]


def changed_dictionary(tmp_path, tag):
    config = Dictionary().content
    config['version'] = f'synthetic-{tag}'
    path = tmp_path / f'{tag}.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path


def reconciled(workbook, tmp_path, rows):
    source = workbook(rows)
    database = tmp_path / 'ab.sqlite'
    first = ingest_workbook(source, database)
    matching = matching_service.reconcile_workbook(source, database)
    assert matching['matching']['status'] == 'ACTIVE'
    return source, database, first, matching


def table_dump(database):
    with closing(connect(database)) as db:
        names = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')] for name in names}


def binding_row(database):
    with closing(connect(database)) as db:
        return db.execute('''SELECT s.match_id,s.snapshot_id,s.status FROM active_matching m
                             JOIN matching_snapshot s ON s.match_id=m.match_id''').fetchone()


def assert_conflict_message(error):
    message = str(error.value)
    assert message.split('：', 1)[0] == CONFLICT_CODE  # 前缀代码必须精确
    assert '不一致' in message and '拒绝' in message and '保持' in message  # 中文说明要素


def test_ab01_approved_refresh_with_active_b_is_rejected(workbook, tmp_path):
    """AB-01（TC-31）：先A→有效B，再批准零输出变化A规则刷新；目标快照不同，激活拒绝。"""
    source, database, first, _ = reconciled(workbook, tmp_path, ROWS)
    changed = changed_dictionary(tmp_path, 'ab01')
    with pytest.raises(ImportRejected, match=CONFLICT_CODE) as error:
        ingest_workbook(source, database, dictionary_path=changed, approved_unchanged_rule_refresh=True)
    assert_conflict_message(error)
    with closing(connect(database)) as db:
        assert db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0] == first['snapshot_id']
    bound = binding_row(database)
    assert bound['snapshot_id'] == first['snapshot_id'] and bound['status'] == 'ACTIVE'


def test_ab02_rejected_refresh_leaves_every_record_unchanged(workbook, tmp_path):
    """AB-02（TC-30）：失败刷新全回滚；来源行、分析/匹配快照和当前指针、配对、额度流水、
    工单/轮次与已存在回执的计数与内容均不变，新增分析无痕迹。"""
    source, database, first, _ = reconciled(workbook, tmp_path, ROWS)
    before = table_dump(database)
    assert all(before[name] for name in AB02_TABLES)  # 计数比较必须覆盖非空业务表
    changed = changed_dictionary(tmp_path, 'ab02')
    with pytest.raises(ImportRejected, match=CONFLICT_CODE):
        ingest_workbook(source, database, dictionary_path=changed, approved_unchanged_rule_refresh=True)
    after = table_dump(database)
    assert {name: len(rows) for name, rows in after.items()} == {name: len(rows) for name, rows in before.items()}
    assert after == before  # 失败刷新零痕迹：连行内容都保持
    with closing(connect(database)) as db:
        assert db.execute('SELECT COUNT(*) FROM analysis_snapshot').fetchone()[0] == 1
        assert db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0] == first['snapshot_id']
    bound = binding_row(database)
    assert bound['snapshot_id'] == first['snapshot_id'] and bound['status'] == 'ACTIVE'


def test_ab03_same_snapshot_reimport_and_retry_stay_idempotent(workbook, tmp_path):
    """AB-03（TC-28/29）：同快照重导/重试返回相同幂等结果，无重复占量/建单。

    除既有契约「每个导入尝试记录一条 import_run」（见 test_tc28_reimport_reuses_source_and_snapshot）
    外，全部业务表内容不变。
    """
    source, database, first, matching = reconciled(workbook, tmp_path, ROWS)
    before = table_dump(database)
    again = ingest_workbook(source, database)
    assert again['reused'] is True and again['snapshot_id'] == first['snapshot_id']
    repeated = ingest_workbook(source, database, approved_unchanged_rule_refresh=True)  # 字典未变→仍复用
    assert repeated['reused'] is True and repeated['snapshot_id'] == first['snapshot_id']
    retry = matching_service.reconcile_workbook(source, database)
    assert retry['matching']['reused'] is True and retry['matching']['match_id'] == matching['matching']['match_id']
    after = table_dump(database)
    assert {n: rows for n, rows in after.items() if n != 'import_run'} == \
        {n: rows for n, rows in before.items() if n != 'import_run'}
    assert len(after['allocation']) == 1 and len(after['ledger_entry']) == 1
    assert len(after['ticket']) == len(before['ticket']) == 1 and len(after['matching_snapshot']) == 1
    assert len(after['import_run']) == len(before['import_run']) + 3


def test_ab04_no_matching_binding_allows_approved_refresh(workbook, tmp_path):
    """AB-04（TC-31）①：无B表或 active_matching 为空时，原已批准的无变化刷新仍可用且激活。"""
    source = workbook([line()])
    database = tmp_path / 'pure-a.sqlite'
    first = ingest_workbook(source, database)
    with closing(connect(database)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'active_matching' not in names  # 纯A库无匹配表：检查不得报错，视为无绑定
    first_change = changed_dictionary(tmp_path, 'ab04-no-table')
    again = ingest_workbook(source, database, dictionary_path=first_change, approved_unchanged_rule_refresh=True)
    assert again['reused'] is False and again['snapshot_id'] != first['snapshot_id']
    assert again['summary']['approved_refresh']['changed_output_rows'] == 0
    with closing(connect(database)) as db:
        assert db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0] == again['snapshot_id']
    initialize_matching(database)  # 匹配表存在但 active_matching 为空（未对账）
    with closing(connect(database)) as db:
        assert db.execute('SELECT COUNT(*) FROM active_matching').fetchone()[0] == 0
    second_change = changed_dictionary(tmp_path, 'ab04-empty-row')
    third = ingest_workbook(source, database, dictionary_path=second_change, approved_unchanged_rule_refresh=True)
    assert third['reused'] is False and third['snapshot_id'] != again['snapshot_id']
    with closing(connect(database)) as db:
        assert db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0] == third['snapshot_id']


def test_ab04_review_snapshot_is_not_a_current_binding(workbook, tmp_path):
    """AB-04（TC-31）②：仅有 REQUIRES_REVIEW 快照、无 active_matching 时，刷新放行；
    待核B不得被误当当前有效B。"""
    source = workbook([line()])
    database = tmp_path / 'review.sqlite'
    first = ingest_workbook(source, database)
    preview = matching_service.match_snapshot(database, rule_version='synthetic-review-version')
    assert preview['status'] == 'REQUIRES_REVIEW'
    with closing(connect(database)) as db:
        assert db.execute('SELECT status FROM matching_snapshot').fetchone()[0] == 'REQUIRES_REVIEW'
        assert db.execute('SELECT COUNT(*) FROM active_matching').fetchone()[0] == 0
    changed = changed_dictionary(tmp_path, 'ab04-review')
    again = ingest_workbook(source, database, dictionary_path=changed, approved_unchanged_rule_refresh=True)
    assert again['reused'] is False and again['snapshot_id'] != first['snapshot_id']
    with closing(connect(database)) as db:
        assert db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0] == again['snapshot_id']
        assert db.execute('SELECT COUNT(*) FROM active_matching').fetchone()[0] == 0
        assert db.execute('SELECT status FROM matching_snapshot').fetchone()[0] == 'REQUIRES_REVIEW'


def test_ab05_conflict_check_holds_the_same_write_transaction(workbook, tmp_path, monkeypatch):
    """AB-05（TC-31/32）：两个激活分支都在已开启的 BEGIN IMMEDIATE 事务内调用检查。

    wrapper 在检查点确认 db.in_transaction=True，并用 timeout=0.05 的第二连接尝试
    BEGIN IMMEDIATE，必须因 'database is locked' 失败；证明检查与激活写之间没有可被
    其他连接穿透的切换窗口（不靠 sleep）。刷新仍被拒。
    """
    source, database, first, _ = reconciled(workbook, tmp_path, ROWS)
    observed = []
    original = ingest_service.ab_binding_conflict

    def wrapper(db, dataset_id, snapshot_id):
        observed.append((db.in_transaction, snapshot_id))
        second = sqlite3.connect(database, timeout=0.05)
        try:
            with pytest.raises(sqlite3.OperationalError, match='database is locked'):
                second.execute('BEGIN IMMEDIATE')
        finally:
            second.close()
        return original(db, dataset_id, snapshot_id)

    monkeypatch.setattr(ingest_service, 'ab_binding_conflict', wrapper)
    reused = ingest_workbook(source, database)  # 复用分支（同快照放行）
    assert reused['reused'] is True and reused['snapshot_id'] == first['snapshot_id']
    changed = changed_dictionary(tmp_path, 'ab05')
    with pytest.raises(ImportRejected, match=CONFLICT_CODE):  # 新快照分支（刷新被拒）
        ingest_workbook(source, database, dictionary_path=changed, approved_unchanged_rule_refresh=True)
    assert len(observed) == 2
    assert all(in_transaction for in_transaction, _ in observed)
    assert observed[0][1] == first['snapshot_id'] and observed[1][1] != first['snapshot_id']


def test_ab05_reuse_activation_branch_rejects_legacy_binding_mismatch(workbook, tmp_path, monkeypatch):
    """AB-05（TC-31/32）：复用分支同样受保护。

    先用既有缺陷行为（检查被绕过）复现历史状态：B有效绑定S1、当前A已是S2；随后以S2
    为目标走复用路径重导，必须被 A_BINDING_CONFLICT_REQUIRES_APPROVAL 拒绝且指针不动。
    """
    source = workbook(ROWS)
    database = tmp_path / 'legacy.sqlite'
    first = ingest_workbook(source, database)
    assert matching_service.reconcile_workbook(source, database)['matching']['status'] == 'ACTIVE'
    changed = changed_dictionary(tmp_path, 'ab05-legacy')
    original = ingest_service.ab_binding_conflict
    monkeypatch.setattr(ingest_service, 'ab_binding_conflict', lambda *args: None)
    legacy = ingest_workbook(source, database, dictionary_path=changed, approved_unchanged_rule_refresh=True)
    assert legacy['reused'] is False and legacy['snapshot_id'] != first['snapshot_id']
    monkeypatch.setattr(ingest_service, 'ab_binding_conflict', original)
    with closing(connect(database)) as db:
        assert db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0] == legacy['snapshot_id']
    bound = binding_row(database)
    assert bound['snapshot_id'] == first['snapshot_id'] and bound['status'] == 'ACTIVE'  # B仍绑定旧A
    with pytest.raises(ImportRejected, match=CONFLICT_CODE):
        ingest_workbook(source, database, dictionary_path=changed, approved_unchanged_rule_refresh=True)
    with closing(connect(database)) as db:
        assert db.execute('SELECT snapshot_id FROM active_analysis').fetchone()[0] == legacy['snapshot_id']
