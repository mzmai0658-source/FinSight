import hashlib
import pytest
from src.utils.original_archive import retain_original,retained_original


def test_replacing_upload_keeps_both_content_versions_and_deduplicates_copies(tmp_path):
    source=tmp_path/'report.pdf';archive=tmp_path/'versions'
    first=b'%PDF-1.7\nfirst original';second=b'%PDF-1.7\nsecond original'
    source.write_bytes(first);sha=hashlib.sha256(first).hexdigest()
    saved=retain_original(source,sha,archive)
    assert retain_original(source,sha,archive)==saved
    source.write_bytes(second);other=retain_original(source,root=archive)
    assert saved!=other and retained_original(sha,archive).read_bytes()==first
    assert other.read_bytes()==second and len(list(archive.rglob('*.pdf')))==2
    with pytest.raises(ValueError):retain_original(source,sha,archive)


def test_tampered_or_missing_archive_is_not_accepted_as_original(tmp_path):
    source=tmp_path/'report.pdf';source.write_bytes(b'%PDF-1.7\noriginal')
    saved=retain_original(source,root=tmp_path/'versions');digest=saved.stem
    saved.write_bytes(b'tampered')
    with pytest.raises(FileNotFoundError):retained_original(digest,tmp_path/'versions')
    with pytest.raises(ValueError):retain_original(source,root=tmp_path/'versions')
    with pytest.raises(ValueError):retained_original('../report',tmp_path/'versions')
