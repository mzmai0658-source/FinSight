"""作品说明：按白名单打包实际工作树，逐文件摘要并直接检查 ZIP；不提交或上传。"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from urllib.parse import unquote, urlsplit
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
DIRECTORIES=('src','config','scripts','tests','backend-java','frontend','shared','demo','eval','database','deploy','monitoring','.github','docs')
ROOT_FILES=('README.md','LICENSE','NOTICE','.gitignore','.gitattributes','.env.example','.env.demo.example','requirements.txt','requirements.lock.txt','pytest.ini','pyproject.toml')
EXCLUDE_DIRS={'node_modules','target','dist','__pycache__','.pytest_cache','.vite','runs'}


def release_files(root):
    files=[root/name for name in ROOT_FILES if (root/name).is_file()]
    for directory in DIRECTORIES:
        for path in (root/directory).rglob('*'):
            if not path.is_file() or any(p in EXCLUDE_DIRS for p in path.relative_to(root).parts):continue
            # 作品说明：旧内部回归数据及其清单仅留本机，不随公开代码包披露。
            if path.relative_to(root).parts[:2] == ('eval','regression_v3'):continue
            # 作品说明：当前验收仅用真实财报，旧合成PDF保留本机历史，不混入本轮代码包。
            if path.relative_to(root).parts[:3] == ('demo','v3','example_reports'):continue
            if path.suffix in {'.pyc','.log','.tmp','.sqlite','.db'} or path.name.startswith('.env'):continue
            if path.name in {'local_keys_private.py'}:continue
            files.append(path)
    return sorted(set(files))


def package(output: Path):
    from scripts.check_release import check_release,SECRET_PATTERNS
    files=release_files(ROOT)
    errors=check_release(ROOT,files=files)
    if errors:raise ValueError('\n'.join(errors))
    identities={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    manifest=dict(format_version=1,files=identities,scope='source and real200 evidence; historical demo definitions retained but synthetic PDFs, private corpus, credentials, weights and runtime excluded',
                  source_sha256=hashlib.sha256(json.dumps(identities,sort_keys=True).encode()).hexdigest())
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for p in files:archive.write(p,p.relative_to(ROOT).as_posix())
        archive.writestr('RELEASE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist())==set(identities)|{'RELEASE_MANIFEST.json'}
        for name,digest in identities.items():
            data=archive.read(name)
            assert hashlib.sha256(data).hexdigest()==digest
            assert '..' not in Path(name).parts and not Path(name).is_absolute()
            if len(data)<2*1024*1024:
                content=data.decode('utf-8',errors='ignore')
                for label,pattern in SECRET_PATTERNS:
                    if pattern.search(content):raise ValueError(f'ZIP contains {label}: {name}')
        # 作品说明：直接核对ZIP内相对文档链接，不依赖工作区里额外存在的历史文件。
        names=set(archive.namelist())
        for name in names:
            if not name.endswith('.md'):continue
            source=archive.read(name).decode('utf-8')
            for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)',source):
                target=target.strip('<>').split(' "')[0]
                parsed=urlsplit(target)
                if parsed.scheme or target.startswith(('#','/')):continue
                import posixpath
                resolved=posixpath.normpath(posixpath.join(posixpath.dirname(name),unquote(parsed.path)))
                if parsed.path and resolved not in names and not any(n.startswith(resolved.rstrip('/')+'/') for n in names):
                    raise ValueError(f'ZIP contains a broken document link: {name} -> {target}')
    checksum=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256').write_text(f'{checksum}  {output.name}\n',encoding='utf-8')
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(zip=str(output),files=len(files),bytes=output.stat().st_size,sha256=checksum,source_sha256=manifest['source_sha256'])))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'deliverables/20261002/FinSight-source.zip')
    package(parser.parse_args().output.resolve())
