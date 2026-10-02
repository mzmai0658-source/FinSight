"""作品说明：固定前瞻公司划分，并准备独立本机验证数据库。"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'data/runtime/real_validation'
CODES=['600080','600085','600129','600222','600252']
PERIODS=[(2022,'FY'),(2023,'FY'),(2023,'HY')]


def main():
    from dotenv import load_dotenv, set_key
    from sqlalchemy import create_engine,text
    from config.db_config import get_db_config, DatabaseConfig
    load_dotenv(ROOT/'.env',override=True)
    admin=get_db_config()
    if admin.host not in {'localhost','127.0.0.1','::1'}:raise RuntimeError('Local DB only')
    engine=create_engine(admin.connection_string)
    with engine.connect() as conn:
        names={r[0]:r[1] for r in conn.execute(text('SELECT stock_code, abbr FROM company'))}
    docs=json.loads((OUT/'catalog.json').read_text(encoding='utf-8'))['documents']
    selected=[]
    for index,code in enumerate(CODES):
        for year,period in PERIODS:
            choices=[r for r in docs if r.get('stock_code')==code and r.get('report_year')==year and r.get('report_period')==period
                     and r.get('is_summary') is False and r.get('page_count',0)>40 and r.get('identity_status')=='cover_candidate']
            if len(choices)!=1:raise ValueError(f'Ambiguous/missing original: {code} {year} {period} ({len(choices)})')
            selected.append({**choices[0],'stock_abbr':names[code], 'split':'development' if index<3 else 'prospective_holdout',
                             'selection_reason':'Preselected complete annual/half-year reports across companies; retained even if ingestion fails',
                             'prior_debug_exposure':'unknown','human_identity_review':'pending'})
    manifest={'created_at_utc':datetime.now(timezone.utc).isoformat(),'documents':selected,
              'split_note':'Prospective company separation for this renovation; historical debugging exposure is unknown. Not proof of unseen pretraining data.',
              'public_distribution':'source URLs and redistribution scope pending; originals remain private local data'}
    path=OUT/'report_manifest.json'
    if path.exists():
        prior=json.loads(path.read_text(encoding='utf-8'))
        if prior['documents']!=selected:raise RuntimeError('Refusing to replace a previously frozen selection')
    else:path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    database='finsight_real_eval'
    control=create_engine(admin.connection_string_no_db)
    with control.begin() as conn:
        conn.execute(text('CREATE DATABASE IF NOT EXISTS finsight_real_eval CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'))
    target=DatabaseConfig(host=admin.host,port=admin.port,user=admin.user,password=admin.password,database=database)
    real=create_engine(target.connection_string)
    from src.init_db import Base
    from src.etl.provenance_store import metadata
    Base.metadata.create_all(real); metadata.create_all(real)
    with real.begin() as conn:
        # 作品说明：只复制表结构，不复制原始报告数据。
        if not admin.database.replace('_','').isalnum():raise ValueError('Invalid source schema')
        conn.execute(text(f'CREATE TABLE IF NOT EXISTS company LIKE `{admin.database}`.company'))
        for code in CODES:
            conn.execute(text(f'INSERT IGNORE INTO company SELECT * FROM `{admin.database}`.company WHERE stock_code=:code'),{'code':code})
            has=conn.execute(text('SELECT COUNT(*) FROM core_performance_indicators_sheet WHERE stock_code=:code'),{'code':code}).scalar()
            if not has:conn.execute(text("UPDATE company SET data_status='pending',first_year=NULL,last_year=NULL WHERE stock_code=:code"),{'code':code})
    env=ROOT/'.env.real-eval'
    if not env.exists():
        user='fs_real_r_'+secrets.token_hex(4);password=secrets.token_urlsafe(36)
        import pymysql
        with pymysql.connect(host=admin.host,port=admin.port,user=admin.user,password=admin.password) as conn:
            with conn.cursor() as cursor:
                cursor.execute('CREATE USER %s@%s IDENTIFIED BY %s',(user,'localhost',password))
                for table in ['company','income_sheet','balance_sheet','cash_flow_sheet','core_performance_indicators_sheet','financial_report_provenance','financial_report_versions']:
                    cursor.execute(f'GRANT SELECT ON `finsight_real_eval`.`{table}` TO %s@%s',(user,'localhost'))
        env.write_text('# Private isolated real-report validation configuration\n',encoding='utf-8')
        settings={'DB_HOST':admin.host,'DB_PORT':str(admin.port),'DB_USER':admin.user,'DB_PASSWORD':admin.password,'DB_NAME':database,
                  'SQL_DB_USER':user,'SQL_DB_PASSWORD':password,'SQL_DB_NAME':database,
                  'MYSQL_HOST':admin.host,'MYSQL_PORT':str(admin.port),'MYSQL_USER':admin.user,'MYSQL_PASSWORD':admin.password,'MYSQL_DATABASE':database,
                  'LLM_PROVIDER':'ollama','LLM_MODEL':'qwen3.5:9b-q4_K_M','OLLAMA_MODEL':'qwen3.5:9b-q4_K_M','OLLAMA_REASONING_EFFORT':'low',
                  'LLM_BASE_URL':'http://127.0.0.1:11434/v1','OLLAMA_BASE_URL':'http://127.0.0.1:11434/v1','OLLAMA_CONTEXT_LENGTH':'16384',
                  'LLM_MAX_TOKENS':'4096','LLM_TIMEOUT_SECONDS':'300','LLM_MAX_RETRIES':'2','ETL_LLM_MAX_WORKERS':'1',
                  'EMBEDDING_PROVIDER':'bge_local','EMBEDDING_MODEL':'BAAI/bge-small-zh-v1.5','EMBEDDING_DEVICE':'cpu','HF_HUB_OFFLINE':'1',
                  'CHROMA_DB_PATH':'data/real_eval_chroma_db','FINANCIAL_FACTS_MANIFEST':'','ETL_INGEST_RAG':'true'}
        for key,value in settings.items():set_key(str(env),key,value)
    print('Frozen 15 original PDFs, 3 development and 2 prospective holdout companies; isolated DB and reader ready.')
    print('Credentials written only to .env.real-eval; original financial_report data unchanged.')


if __name__=='__main__':main()
