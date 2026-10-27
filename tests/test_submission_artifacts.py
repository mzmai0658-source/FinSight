"""文件冻结与来源验收不能依赖旧公司名单或手动改摘要。"""
from hashlib import sha256
import pytest


def test_artifact_hashes_are_root_relative_and_tampering_is_detected(tmp_path):
    from src.etl.submission_artifacts import freeze_artifacts, verify_artifacts
    file=tmp_path/'data/runtime/submissions/assets'/('a'*64)/'facts.json'
    file.parent.mkdir(parents=True)
    file.write_text('[]',encoding='utf-8')
    frozen=freeze_artifacts([file],root=tmp_path)
    assert list(frozen)==['data/runtime/submissions/assets/'+('a'*64)+'/facts.json']
    verify_artifacts(frozen,root=tmp_path,asset_id='a'*64)
    file.write_text('[1]',encoding='utf-8')
    with pytest.raises(ValueError):
        verify_artifacts(frozen,root=tmp_path,asset_id='a'*64)


def test_artifact_paths_cannot_escape_asset_directory(tmp_path):
    from src.etl.submission_artifacts import verify_artifacts
    secret=tmp_path/'private.env'
    secret.write_text('not-a-real-key')
    with pytest.raises(ValueError):
        verify_artifacts({'private.env':sha256(secret.read_bytes()).hexdigest()},root=tmp_path,asset_id='a'*64)
