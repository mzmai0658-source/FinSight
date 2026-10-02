"""作品说明：在隔离数据库中准备公开 PDF、事实与索引，通过统一验收后发布。"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def allowed_database(name: str, host: str) -> bool:
    return bool(re.fullmatch(r'finsight_demo(?:_[a-zA-Z0-9_]+)?', name)) and host in {'127.0.0.1', 'localhost', '::1'}


def bootstrap(env_file: Path):
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text, select
    load_dotenv(env_file, override=True)
    from config.db_config import get_db_config
    config = get_db_config()
    if not allowed_database(config.database, config.host):
        raise ValueError('仅允许本机 finsight_demo 或 finsight_demo_* 隔离库，不修改现用真实库')
    import pymysql
    with pymysql.connect(host=config.host, port=config.port, user=config.user, password=config.password) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{config.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    from demo.v3.generate import generate
    folder = Path(generate()["folder"])
    subprocess.run([sys.executable, str(ROOT/'scripts/build_canonical_index.py'), '--env', str(env_file), '--audit', str(folder)], check=True)
    from scripts.accept_canonical_release import validate
    manifest, reports, facts, index, acceptance = validate(folder)
    (folder/'acceptance.json').write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding='utf-8')
    engine = create_engine(config.connection_string)
    from src.init_db import Base
    from src.agent.v3.repository import metadata, releases, pointer, stage_release, publish_release
    from src.etl.provenance_store import metadata as provenance_metadata
    from src.etl.canonical_projection import apply_projections
    from scripts.seed_demo_database import COMPANY_TABLE_SQL, COMPANY_SEED_SQL
    Base.metadata.create_all(engine)
    metadata.create_all(engine)
    provenance_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(COMPANY_TABLE_SQL))
    with engine.connect() as conn:
        serving=conn.execute(select(pointer.c.version).where(pointer.c.name=='serving')).scalar_one_or_none()
        old=conn.execute(select(releases.c.status).where(releases.c.version==manifest['version'])).scalar_one_or_none()
    if old=='accepted' and serving==manifest['version']:
        print('相同演示版本已发布；事实与索引不重复写入。')
    elif old=='accepted':
        raise ValueError('此版本已验收但不在现用指针，需明确处理版本回切，不自动覆盖')
    else:
        stage_release(engine,manifest['version'],facts,reports,index['collection'],manifest)
        def project(conn):
            apply_projections(conn,facts,reports)
            conn.execute(text(COMPANY_SEED_SQL))
        publish_release(engine,manifest['version'],acceptance,project=project)
    engine.dispose()
    print(json.dumps({'published':manifest['version'],'profile':manifest['dataset_profile'],'facts':len(facts)},ensure_ascii=False))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env.demo')
    bootstrap(parser.parse_args().env_file.resolve())
