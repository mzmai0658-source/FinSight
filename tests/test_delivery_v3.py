"""作品说明：公开数据也必须满足原页核验，覆盖、权限与失败发布不能被演示身份绕过。"""
import json
from pathlib import Path
import pytest
from src.etl.release_profiles import DEMO, check_report_coverage
from scripts.bootstrap_demo_v3 import allowed_database
from src.agent.v3.contracts import Fact
from src.agent.v3.evidence import checked_source


def test_demo_coverage_rejects_duplicate_missing_and_wrong_period():
    rows=[dict(stock_code=c,year=y,period='FY') for c in DEMO.codes for y in (2022,2023,2024)]
    manifest=dict(dataset_profile=DEMO.public_metadata(),reports=15)
    assert check_report_coverage(manifest,rows)==DEMO
    for bad in (rows[:-1],rows+[rows[0]],[{**r,'period':'H1'} for r in rows]):
        with pytest.raises(ValueError):check_report_coverage(manifest,bad)
    with pytest.raises(ValueError):check_report_coverage({**manifest,'dataset_profile':{**manifest['dataset_profile'],'reports':14}},rows)


@pytest.mark.parametrize('name,host,expected',[
    ('finsight_demo','localhost',True),('finsight_demo_delivery_20261002','127.0.0.1',True),
    ('financial_report','localhost',False),('finsight_demo;DROP DATABASE x','localhost',False),
    ('finsight_demo','example.com',False)])
def test_demo_database_isolation(name,host,expected):
    assert allowed_database(name,host)==expected


def test_public_pdf_generation_and_tampering(tmp_path):
    from demo.v3.generate import generate, ROOT
    # 作品说明：生成真实可打开 PDF；输出审核清单位于工作区中，测试结束不发布数据库指针。
    folder=ROOT/'data/runtime/demo-v3-test'
    first=generate(folder);second=generate(folder)
    assert first['version']==second['version']
    raw=json.loads((folder/'facts.json').read_text('utf-8'))
    assert len(raw)==240
    fact=Fact.model_validate(raw[0])
    assert checked_source(fact)
    assert not checked_source(fact.model_copy(update={'value':'0'}))
    assert not checked_source(fact.model_copy(update={'scope':'parent'}))
    changed=fact.source.model_copy(update={'raw_unit':'元'})
    assert not checked_source(fact.model_copy(update={'source':changed}))


def test_task_store_first_creation_keeps_workspace_boundary(tmp_path,monkeypatch):
    from src.api import main
    from src.agent.v3.tasks import TaskStore
    from fastapi import HTTPException
    monkeypatch.setattr(main,'ROOT_DIR',tmp_path)
    path=main._new_workspace_file('fresh/tasks.sqlite3')
    assert not path.exists()
    store=TaskStore(path)
    assert path.is_file()
    with pytest.raises(HTTPException):main._new_workspace_file('../outside.sqlite3')
    with pytest.raises(HTTPException):main._new_workspace_file('.')


def test_condition_verbs_are_not_company_names():
    from src.agent.v3.request_bindings import unregistered_company_literals
    for verb in ('改查','改为查','换成查','重新查','继续查','接着查','再查'):
        assert not unregistered_company_literals(verb+'2023年营业收入',{})
        assert unregistered_company_literals(verb+'创新科技2023年营业收入',{})==['创新科技']
    assert unregistered_company_literals('贵州茅台2024年营业收入',{})==['贵州茅台']


def test_seed_conflicting_database_stops_before_dependency_or_account_changes(tmp_path):
    import shutil, subprocess
    shell=shutil.which('pwsh')
    if not shell:pytest.skip('PowerShell 7 not available')
    env_file=tmp_path/'demo.env'
    env_file.write_text('DB_NAME=finsight_demo_first\nMYSQL_DATABASE=finsight_demo_first\n',encoding='utf-8')
    script=Path(__file__).resolve().parents[1]/'scripts/demo-seed.ps1'
    result=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(script),'-EnvFile',str(env_file),'-Database','finsight_demo_other','-SkipDatabase','-SkipModelPull'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
    assert result.returncode!=0
    assert 'Database must match DB_NAME' in result.stderr
    assert 'Seeding isolated' not in result.stdout


def test_clean_copy_does_not_load_parent_private_environment(tmp_path):
    import os, subprocess, sys
    parent=tmp_path/'parent';project=parent/'project';config=project/'config';config.mkdir(parents=True)
    (parent/'.env').write_text('DB_NAME=private_parent_database\n',encoding='utf-8')
    source=Path(__file__).resolve().parents[1]/'config/db_config.py'
    (config/'db_config.py').write_bytes(source.read_bytes())
    env={k:v for k,v in os.environ.items() if not k.startswith(('DB_', 'MYSQL_'))}
    result=subprocess.run([sys.executable,'-c','from config.db_config import get_db_config; print(get_db_config().database)'],cwd=project,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0
    assert result.stdout.strip()=='financial_report'
