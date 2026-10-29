import json
from pathlib import Path
import pytest
from scripts.restore_release import TABLES, verify, sha


def transport(folder):
    records=folder/'records.json'
    records.write_text(json.dumps({k:[] for k in TABLES}),encoding='utf-8')
    manifest=dict(schema=1,users_exported=False,tables={k:0 for k in TABLES},
                  collections=[],files={'records.json':sha(records)})
    (folder/'transport.json').write_text(json.dumps(manifest),encoding='utf-8')
    return manifest


def test_transport_rejects_changed_financial_snapshot(tmp_path):
    transport(tmp_path)
    (tmp_path/'records.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='identity mismatch'):verify(tmp_path)


def test_transport_rejects_unlisted_private_file(tmp_path):
    transport(tmp_path)
    (tmp_path/'.env').write_text('PRIVATE_CONFIGURATION',encoding='utf-8')
    with pytest.raises(ValueError,match='inventory differs'):verify(tmp_path)


def test_transport_never_accepts_user_export_claim(tmp_path):
    manifest=transport(tmp_path);manifest['users_exported']=True
    (tmp_path/'transport.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError,match='Unsupported transport'):verify(tmp_path)


def test_transport_rejects_external_vector_path(tmp_path):
    manifest=transport(tmp_path)
    manifest['collections']=[dict(name='financial_test',file='../outside.jsonl.gz',count=1,metadata={})]
    (tmp_path/'transport.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError,match='Unsafe vector artifact'):verify(tmp_path)
