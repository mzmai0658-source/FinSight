"""作品说明：按内容身份保存原始 PDF 字节，使上传与历史引用可追溯。"""
import hashlib
import os
from pathlib import Path
import tempfile

ROOT=Path(__file__).resolve().parents[2]
ARCHIVE=ROOT/'data_root'/'original_versions'


def digest_file(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def archive_path(digest,root=None):
    if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Invalid document content identity')
    return (root or ARCHIVE)/digest[:2]/(digest+'.pdf')


def retain_original(path,expected_digest=None,root=None):
    path=Path(path)
    digest=digest_file(path)
    if expected_digest and digest!=expected_digest:raise ValueError('Original document identity changed')
    target=archive_path(digest,root)
    if target.is_file():
        if digest_file(target)!=digest:raise ValueError('Retained original is corrupt')
        return target
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with path.open('rb') as source,tempfile.NamedTemporaryFile(dir=target.parent,suffix='.tmp',delete=False) as saved:
            temporary=Path(saved.name)
            while chunk:=source.read(1024*1024):saved.write(chunk)
            saved.flush();os.fsync(saved.fileno())
        if digest_file(temporary)!=digest:raise ValueError('Original changed during retention')
        os.replace(temporary,target);temporary=None
        return target
    finally:
        if temporary and temporary.exists():temporary.unlink()


def retained_original(digest,root=None):
    target=archive_path(digest,root)
    if not target.is_file() or digest_file(target)!=digest:raise FileNotFoundError('Retained original missing or changed')
    return target
