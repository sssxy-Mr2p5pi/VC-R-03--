"""Technical sample-rebuild tests; synthetic archives, separate from A business tests."""
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from rebuild_performance_sample import capture_recipe,restore_archive,rebuild_sample,file_hash  # noqa: E402


def archive(path,*,core='original metadata',data='synthetic fixture'):
    with ZipFile(path,'w',compression=8) as z:
        z.writestr('docProps/core.xml',core)
        z.writestr('sheet.xml',data)
    return path


def test_rebuild_archive_byte_identity_and_original_preserved(tmp_path):
    original=archive(tmp_path/'original.zip')
    recipe=capture_recipe(original)
    generated=archive(tmp_path/'generated.zip',core='new metadata')
    before=file_hash(generated)
    target=tmp_path/'restored.zip'
    restore_archive(generated,target,recipe)
    assert file_hash(target)==file_hash(original)==recipe['sample_sha256']
    assert file_hash(generated)==before


def test_rebuild_changed_payload_rejected_before_publish(tmp_path):
    original=archive(tmp_path/'original.zip')
    generated=archive(tmp_path/'generated.zip',data='modified fixture')
    target=tmp_path/'restored.zip'
    with pytest.raises(ValueError,match='生成内容哈希不一致'):
        restore_archive(generated,target,capture_recipe(original))
    assert not target.exists()


def test_rebuild_wrong_whole_archive_hash_no_publish(tmp_path):
    generated=archive(tmp_path/'generated.zip')
    recipe=capture_recipe(generated)
    recipe['sample_sha256']='0'*64
    target=tmp_path/'restored.zip'
    with pytest.raises(ValueError,match='字节哈希无法复现'):
        restore_archive(generated,target,recipe)
    assert not target.exists()


def test_rebuild_existing_output_preserved(tmp_path):
    output=tmp_path/'sample.xlsx'
    output.write_bytes(b'existing sample')
    with pytest.raises(ValueError,match='禁止覆盖'):
        rebuild_sample(tmp_path/'missing.xlsx',output,tmp_path/'evidence')
    assert output.read_bytes()==b'existing sample'


@pytest.mark.parametrize('bad_field,error',[
    ('source_sha256','源哈希不一致'),('mapping_sha256','来源映射哈希不一致')])
def test_rebuild_source_or_mapping_hash_rejected(tmp_path,bad_field,error):
    source=tmp_path/'source.xlsx'
    source.write_bytes(b'synthetic source, never read as a workbook')
    evidence=tmp_path/'evidence'
    evidence.mkdir()
    mapping=evidence/'source_rows.csv'
    mapping.write_bytes(b'synthetic mapping')
    manifest={'source_sha256':file_hash(source),'mapping_sha256':file_hash(mapping),'sample_sha256':'1'*64}
    manifest[bad_field]='0'*64
    (evidence/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    (evidence/'rebuild-metadata.json').write_text('{}',encoding='utf-8')
    before={p.name:p.read_bytes() for p in evidence.iterdir()}
    output=tmp_path/'out.xlsx'
    with pytest.raises(ValueError,match=error):
        rebuild_sample(source,output,evidence)
    assert not output.exists() and before=={p.name:p.read_bytes() for p in evidence.iterdir()}


def test_rebuild_missing_metadata_refuses_reselection(tmp_path):
    evidence=tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'manifest.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='停止，不重新选样'):
        rebuild_sample(tmp_path/'source.xlsx',tmp_path/'output.xlsx',evidence)
    assert sorted(p.name for p in evidence.iterdir())==['manifest.json']
