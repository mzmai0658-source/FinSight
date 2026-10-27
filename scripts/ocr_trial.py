"""本地宽松OCR入库与少量真实问答，复用新OCR、云端向量并保留原件审核状态。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path,value):
    from src.etl.ocr_client import _atomic_write
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    _atomic_write(Path(path),(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def environment():
    from src.etl.submission_upgrade import load_environment
    values=load_environment(ROOT)
    os.environ.update(values)
    return values


def engines():
    from sqlalchemy import create_engine
    from config.db_config import get_db_config,get_readonly_db_config
    return (create_engine(get_db_config().connection_string,pool_pre_ping=True),
            create_engine(get_readonly_db_config().connection_string,pool_pre_ping=True))


def folder():
    return ROOT/'data/runtime/submissions/ocr_trial'


def current():
    return read(folder()/'current.json')


def extraction_implementation():
    from src.etl.submission_artifacts import PIPELINE_FILES,pipeline_digest
    import shutil
    extra=('scripts/ocr_trial.py','src/etl/ocr_trial.py','src/etl/ocr_quality.py','src/agent/v3/evidence.py',
           'src/utils/ocr_json_parser.py','src/utils/original_archive.py','src/agent/v3/repository.py')
    mode=os.getenv('PDF_TEXT_EXTRACTOR','pymupdf')
    executable=(os.getenv('PDFTOTEXT_EXECUTABLE','').strip() or shutil.which('pdftotext')) if mode not in {'pymupdf','pypdf'} else None
    return dict(pipeline_digest=pipeline_digest(),sources={name:sha(ROOT/name) for name in (*PIPELINE_FILES,*extra)},
                original_text_extractor=mode,pdftotext_sha256=sha(executable) if executable else None)


def prepare():
    from src.etl.submission_service import get_store
    from src.etl.ocr_quality import extract_fresh_document,field_coverage,validate_trial_dataset
    from src.agent.v3.contracts import Fact
    from src.agent.v3.repository import identity_digest
    from src.etl.submission_index import narrative_chunks
    from src.agent.providers import resolve_embedding_function,embedding_fingerprint
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    sources=read(ROOT/'data/runtime/cloud_upload120_sources.json')['reports']
    assets={row['candidate_id']:row for row in get_store().list()}
    selected=[];skipped=[]
    for row in sorted(sources,key=lambda item:item['report_id']):
        asset=assets.get(row['sha256']);asset_folder=ROOT/'data/runtime/submissions/assets'/row['sha256']
        ocr=asset_folder/'ocr.json'
        if not asset or not ocr.is_file():
            skipped.append(dict(report_id=row['report_id'],reason='fresh_ocr_not_available'))
            continue
        meta=read(str(ocr)+'.meta.json')
        reservation=get_store().ocr_submission(row['sha256'])
        if (meta.get('pdf_sha256')!=row['sha256'] or meta.get('cache_sha256')!=sha(ocr)
                or meta.get('origin')!='remote_ocr' or not reservation or not reservation.get('job_id')):
            raise ValueError('Fresh OCR provenance is incomplete: '+row['report_id'])
        original=ROOT/asset['source_path']
        if sha(original)!=row['sha256']:
            raise ValueError('Original identity changed: '+row['report_id'])
        selected.append((row,asset,ocr))
    policy_inputs=extraction_implementation()
    inventory=[dict(report_id=row['report_id'],document_version=row['sha256'],ocr_sha256=sha(ocr)) for row,asset,ocr in selected]
    version=identity_digest(dict(policy='fresh-ocr-full-reextraction-v2',inventory=inventory,implementation=policy_inputs))
    work=folder()/version
    work.mkdir(parents=True,exist_ok=True)
    all_facts=[];reports=[];narratives=[];summary=[]
    for ordinal,(row,asset,ocr) in enumerate(selected,1):
        started=time.monotonic()
        report={key:row[key] for key in ('stock_code','company','year','period','page_count')}
        report.update(document_version=row['sha256'],source_sha256=row['sha256'],source_path=asset['source_path'],
                      source_url=row.get('source_url'),identity_basis='official_manifest_user_authorized_trial')
        path=work/'documents'/(row['sha256']+'.json')
        if path.exists():
            document=read(path)
            if (document.get('mode')!='fresh_ocr_full_reextraction' or document.get('source_sha256')!=row['sha256']
                or document.get('ocr_sha256')!=sha(ocr) or document.get('implementation')!=policy_inputs
                or document.get('report')!=report
                or any(value.get('data_version')!=version for value in document.get('facts',[]))):
                raise ValueError('Cached reextraction fingerprint or identity changed: '+row['report_id'])
        else:
            print(json.dumps(dict(stage='extracting',position=ordinal,total=len(selected),report_id=row['report_id']),ensure_ascii=False),flush=True)
            facts,ledger,narrative=extract_fresh_document(report,version,ocr)
            for fact in facts:
                fact.data_version=version
                fact.company=row['company']
            # 保留冲突排除，不将百分比冲突候选替换成收入金额。
            document=dict(report=report,facts=[fact.model_dump(mode='json') for fact in facts],narratives=narrative,
                          ledger=ledger,mode='fresh_ocr_full_reextraction',ocr_sha256=sha(ocr),source_sha256=row['sha256'],
                          implementation=policy_inputs,field_coverage=field_coverage(facts,ledger))
            write(path,document)
        reports.append(document['report']);all_facts.extend(document['facts']);narratives.extend(document['narratives'])
        item=dict(report_id=row['report_id'],facts=len(document['facts']),pages=len(document['narratives']),
                  mode=document['mode'],statuses=dict(Counter(f['status'] for f in document['facts'])),seconds=round(time.monotonic()-started,3))
        summary.append(item)
        print(json.dumps(dict(stage='prepare',position=ordinal,total=len(selected),**item),ensure_ascii=False),flush=True)
    if not all_facts or not narratives:raise ValueError('No fresh OCR data available for the trial')
    validation=validate_trial_dataset([Fact.model_validate(value) for value in all_facts],reports,version,
        progress=lambda value:print(json.dumps(value,ensure_ascii=False),flush=True))
    chunks=narrative_chunks(narratives)
    embedding=resolve_embedding_function('ingest')
    from src.etl.submission_index import CHUNKER_VERSION
    collection='financial_cloud_'+identity_digest(dict(version=version,fingerprint=embedding_fingerprint(embedding),
                    chunks=identity_digest([chunk['id'] for chunk in chunks]),chunker=CHUNKER_VERSION))[:32]
    manifest=dict(version=version,review_mode='relaxed_ocr',reports=len(reports),facts=len(all_facts),
                  source_audit_complete=False,scope_audit_complete=False,ocr_reused=True,
                  dataset_profile=dict(id='ocr-trial-v1',kind='real',reports=len(reports),
                      companies=len({report['stock_code'] for report in reports}),inventory=reports),
                  export_path=work.relative_to(ROOT).as_posix(),skipped=skipped,chunk_count=len(chunks),
                  implementation=policy_inputs,expected_source_reports=len(sources),extraction_validation=validation,
                  reextracted_reports=len(reports),previous_extraction_reused=False)
    write(work/'reports.json',reports);write(work/'facts.json',all_facts)
    write(work/'narratives.json',narratives);write(work/'summary.json',summary);write(work/'manifest.json',manifest)
    info=dict(version=version,work=work.relative_to(ROOT).as_posix(),collection=collection,reports=len(reports),
              facts=len(all_facts),statuses=dict(Counter(f['status'] for f in all_facts)),chunks=len(chunks),skipped=skipped)
    writer,reader=engines()
    try:
        from sqlalchemy import select
        from src.agent.v3.repository import stage_release,releases
        with writer.connect() as conn:status=conn.execute(select(releases.c.status).where(releases.c.version==version)).scalar_one_or_none()
        if status!='experimental':
            stage_release(writer,version,[Fact.model_validate(value) for value in all_facts],reports,collection,manifest)
    finally:writer.dispose();reader.dispose()
    write(folder()/'current.json',info)
    return info


def index():
    from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
    from src.etl.submission_index import build_index,narrative_chunks,_vectors_ok,CHUNKER_VERSION
    from src.agent.providers import resolve_embedding_function,assert_collection_embedding,build_collection_metadata
    from src.agent.v3.repository import identity_digest
    from src.agent.tool_shared import resolve_chroma_db_path
    import chromadb
    info=current();work=ROOT/info['work']
    client=chromadb.PersistentClient(path=str(resolve_chroma_db_path()))
    embedding=resolve_embedding_function('ingest')
    versions={report['document_version'] for report in read(work/'reports.json')}
    reusable=[]
    for collection in client.list_collections():
        metadata=collection.metadata or {}
        if metadata.get('data_version') not in versions and metadata.get('index_scope')!='release':continue
        try:assert_collection_embedding(collection,embedding)
        except ValueError:continue
        reusable.append(collection.name)
    last=[0.0]
    def heartbeat():
        now=time.monotonic()
        if now-last[0]>=15:
            last[0]=now
            try:count=client.get_collection(info['collection']).count()
            except Exception:count=0
            print(json.dumps(dict(stage='index',stored_chunks=count,total=info['chunks'],resume=True)),flush=True)
    narratives=read(work/'narratives.json');chunks=narrative_chunks(narratives)
    metadata=build_collection_metadata(embedding,dict(data_version=info['version'],chunker_version=CHUNKER_VERSION,
        index_scope='release',source_inventory_sha256=identity_digest([row['id'] for row in chunks]),dimensions=embedding.dimensions))
    target=client.get_or_create_collection(info['collection'],metadata=metadata,embedding_function=None)
    if target.metadata!=metadata:raise ValueError('Trial index immutable metadata changed')
    present=set(target.get(include=[])['ids']);existing_count=len(present)
    missing={row['id']:row for row in chunks if row['id'] not in present}
    reused_count=0
    # 先按原件批量复用已完成向量，避免每个小批次反复打开全部历史集合。
    for old_name in reusable:
        if not missing:break
        if old_name==info['collection']:continue
        old=client.get_collection(old_name,embedding_function=None)
        eligible=[identifier for identifier,row in missing.items() if old.metadata.get('index_scope')!='document'
                  or old.metadata.get('data_version')==row['metadata']['document_version']]
        for start in range(0,len(eligible),200):
            ids=eligible[start:start+200]
            values=old.get(ids=ids,include=['documents','metadatas','embeddings'])
            accepted=[];vectors=[]
            for identifier,text,meta,vector in zip(values['ids'],values['documents'],values['metadatas'],values['embeddings']):
                row=missing.get(identifier)
                if row is None or row['text']!=text or row['metadata']!=meta:continue
                numeric=list(map(float,vector))
                if not _vectors_ok([numeric],1,embedding.dimensions):raise ValueError('Reused vector is invalid')
                accepted.append(row);vectors.append(numeric)
            if accepted:
                target.upsert(ids=[r['id'] for r in accepted],documents=[r['text'] for r in accepted],
                              metadatas=[r['metadata'] for r in accepted],embeddings=vectors)
                for row in accepted:missing.pop(row['id'])
                reused_count+=len(accepted)
            heartbeat()
    pending_rows=list(missing.values());batch_size=min(10,embedding.max_batch_size)
    batches=iter(pending_rows[i:i+batch_size] for i in range(0,len(pending_rows),batch_size))
    new_count=0;calls=0
    # 最多三个独立网络请求并行；首个失败后不再提交新批次，已写入向量可续跑。
    with ThreadPoolExecutor(max_workers=3,thread_name_prefix='ocr-trial-embedding') as pool:
        running={}
        def submit():
            nonlocal calls
            batch=next(batches,None)
            if batch is None:return False
            running[pool.submit(embedding.embed_documents,[row['text'] for row in batch])]=batch
            calls+=1;return True
        for _ in range(3):submit()
        try:
            while running:
                ready,_=wait(running,timeout=1,return_when=FIRST_COMPLETED)
                heartbeat()
                for future in ready:
                    batch=running.pop(future);vectors=future.result()
                    if not _vectors_ok(vectors,len(batch),embedding.dimensions):raise ValueError('New embedding vectors are invalid')
                    target.upsert(ids=[r['id'] for r in batch],documents=[r['text'] for r in batch],
                                  metadatas=[r['metadata'] for r in batch],embeddings=vectors)
                    new_count+=len(batch)
                for _ in ready:submit()
        except BaseException:
            for future in running:future.cancel()
            raise
    # 仍由同一正式索引验收函数检查全文、来源、覆盖和向量，不能用条数替代验收。
    result=build_index(narratives,info['version'],embedding=embedding,client=client,
                       heartbeat=heartbeat,index_scope='release')
    result.update(existing_before_run=existing_count,reused_vectors_this_run=reused_count,
                  new_vectors_this_run=new_count,embedding_calls_this_run=calls)
    if result['collection']!=info['collection']:raise ValueError('Trial index collection changed')
    write(work/'index.json',result)
    return result


def publish():
    from src.etl.ocr_trial import publish_trial
    from src.etl.ocr_quality import validate_staged_snapshot
    from src.etl.release_profiles import check_report_coverage
    info=current();work=ROOT/info['work'];index_result=read(work/'index.json')
    manifest=read(work/'manifest.json');reports=read(work/'reports.json')
    check_report_coverage(manifest,reports)
    if not index_result['accepted'] or index_result['collection']!=info['collection'] or index_result['chunks']!=info['chunks']:
        raise ValueError('Index acceptance does not match the frozen trial')
    import chromadb
    from src.agent.tool_shared import resolve_chroma_db_path
    client=chromadb.PersistentClient(path=str(resolve_chroma_db_path()))
    if client.get_collection(info['collection']).count()!=info['chunks']:raise ValueError('Trial index is incomplete')
    writer,reader=engines()
    try:
        def validate(conn):
            return validate_staged_snapshot(conn,info,manifest,read(work/'facts.json'),reports,
                progress=lambda value:print(json.dumps(value,ensure_ascii=False),flush=True))
        publish_trial(writer,info['version'],dict(inventory_checked=True,index_verified=True),validate_snapshot=validate)
    finally:writer.dispose();reader.dispose()
    value=dict(status='trial_ready',**info,published_at=datetime.now(timezone.utc).isoformat(),source_audit_complete=False)
    write(work/'publication.json',value)
    return value


def enable():
    # 只改隔离环境的一项开关；正式数据库和日常服务不切换。
    from src.etl.submission_upgrade import load_environment
    load_environment(ROOT)
    info=current()
    if not (ROOT/info['work']/'publication.json').is_file():raise ValueError('Publish the trial index before enabling')
    path=ROOT/'.env.upload120'
    old=path.read_text(encoding='utf-8')
    backup=ROOT/'.local_runtime/upgrade120/env-before-ocr-trial.private'
    if not backup.exists():backup.write_text(old,encoding='utf-8')
    items=[line for line in old.splitlines() if not line.startswith('FINSIGHT_OCR_TRIAL=')]
    path.write_text('\n'.join([*items,'FINSIGHT_OCR_TRIAL="true"'])+'\n',encoding='utf-8')
    return dict(status='enabled_in_isolated_configuration',restart_required=True,version=info['version'])


def chart_preview(result):
    parts=[]
    for chart in result.get('chart_data_list',[]):
        parts.append('<p>已返回图表：'+html.escape(chart.get('chart_type',''))+'；单位：'+html.escape(chart.get('unit',''))+'</p>')
        parts.append('<table border="1" cellspacing="0" cellpadding="8"><tr><th>公司</th><th>期间</th><th>数值</th></tr>')
        for series in chart.get('series',[]):
            for index,label in enumerate(chart.get('x',[])):
                values=series.get('values_formatted') or series.get('values_exact') or series.get('values',[])
                text=str(values[index]) if index<len(values) else '缺失'
                parts.append('<tr><td>'+html.escape(series['name'])+'</td><td>'+html.escape(label)+'</td><td>'+html.escape(text)+'</td></tr>')
        parts.append('</table>')
    return ''.join(parts)


def smoke_questions():
    return [
        ('A', '美的集团2023年年报的扣非归母净利润是多少？按亿元保留两位小数。'),
        ('A', '换成比亚迪，改看2024年年报的归母净利润，单位和精度照旧。'),
        ('B', '贵州茅台2023年年报的合并营业收入是多少？按亿元保留两位小数。'),
        ('C', '宁德时代2024年年报的合并营业收入是多少？按亿元保留两位小数。'),
        ('D', '中国建筑2023年一季报的合并营业收入是多少？用亿元表示。'),
        ('E', '用柱状图比较美的集团和比亚迪2025年一季报的合并营业收入，单位亿元。'),
        ('F', '美的集团2023年年报的扣非归母净利润出自原件第几页？请给出处。'),
        ('G', '你能帮我做什么？'),
        ('H', '比亚迪2025年一季报中，营业收入变动的原因是什么？请找报告中的依据，找不到就明确说明。'),
    ]


def cloud_failure(value):
    task=value.get('task',value)
    result=value.get('result') or task.get('result') or {}
    reasons=[value.get('reason'),*(result.get('outcome') or {}).get('reason_codes',[])]
    known={'cloud_api_key_required','cloud_authentication_or_balance_failed','cloud_rate_limited',
           'cloud_model_or_endpoint_not_found','cloud_service_unavailable','cloud_service_request_failed',
           'cloud_connection_or_response_failed','cloud_response_model_mismatch','cloud_invalid_response','model_timeout'}
    return next((reason for reason in reasons if isinstance(reason,str) and reason in known),None)


def smoke(env):
    from eval.cloud_upload200 import JavaSSEClient
    import uuid
    info=current();work=ROOT/info['work'];output=work/'smoke';output.mkdir(exist_ok=True)
    questions=smoke_questions()
    cases_path=output/'cases.json'
    if cases_path.exists():
        if read(cases_path)!=[list(value) for value in questions]:raise ValueError('Frozen smoke questions changed')
    else:write(cases_path,questions)
    client=JavaSSEClient('http://127.0.0.1:18280',env['UPLOAD200_USERNAME'],env['UPLOAD200_PASSWORD'])
    sessions_path=output/'sessions.json'
    sessions=read(sessions_path) if sessions_path.exists() else {}
    consecutive=0;pause_reason=None
    for ordinal,(group,question) in enumerate(questions,1):
        record=output/f'{ordinal:02d}.json'
        if record.exists():continue
        if group not in sessions:
            response=client.call('POST','/api/chat/sessions',json={'title':'OCR宽松试验 '+group})
            sessions[group]=response.get('sessionUid') or response.get('session_uid') or response.get('uid')
            if not sessions[group]:raise ValueError('No Java session identity returned')
            write(sessions_path,sessions)
        intent=output/f'{ordinal:02d}.intent.json'
        if not intent.exists():write(intent,dict(question=question,session_uid=sessions[group],client_request_id=str(uuid.uuid4())))
        request=read(intent)
        result=client.turn(request['question'],request['session_uid'],request['client_request_id'])
        write(record,result)
        print(json.dumps(dict(stage='smoke',position=ordinal,question=question,status='recorded'),ensure_ascii=False),flush=True)
        reason=cloud_failure(result)
        consecutive=consecutive+1 if reason else 0
        if reason in {'cloud_api_key_required','cloud_authentication_or_balance_failed','cloud_model_or_endpoint_not_found'} or consecutive>=2:
            pause_reason=reason;break
    records=[]
    for ordinal,(_,question) in enumerate(questions,1):
        if not (output/f'{ordinal:02d}.json').exists():break
        value=read(output/f'{ordinal:02d}.json')
        # JavaSSEClient保留完整任务、事件和实际保存状态；此处生成可直接阅读的答案页。
        task=value.get('task',value)
        result=value.get('result') or task.get('result') or {}
        records.append(dict(question=question,raw_file=f'{ordinal:02d}.json',result=result,
                            task_status=task.get('status'),saved=task.get('saved')))
    write(output/'summary.json',records)
    parts=['<!doctype html><meta charset="utf-8"><title>OCR宽松入库问答记录</title><style>body{font:16px/1.7 system-ui;max-width:1100px;margin:32px auto;padding:20px}pre{white-space:pre-wrap;background:#f4f6f8;padding:18px}article{border-top:1px solid #ccc;padding:20px 0}</style>',
           f'<h1>真实财报重提取：{len(questions)}轮Java/SSE问答</h1><p>本轮验证处理链路，逐项保留已核验与待核验来源。小样例不作为正式准确率；中断时保留首次记录。</p>']
    for ordinal,row in enumerate(records,1):
        result=row['result'];answer=(result.get('answer') or {}).get('content','')
        parts.append(f'<article><h2>{ordinal}. {html.escape(row["question"])}</h2><pre>{html.escape(answer)}</pre>{chart_preview(result)}<p>结果状态：{html.escape(str(result.get("outcome")))}</p><details><summary>事实、核验与诊断</summary><pre>{html.escape(json.dumps(result,ensure_ascii=False,indent=2))}</pre></details></article>')
    (output/'answers.html').write_text('\n'.join(parts),encoding='utf-8')
    return dict(status='paused' if pause_reason else 'smoke_recorded',turns=len(records),planned=len(questions),
                reason=pause_reason,answers=str(output/'answers.html'))


def smoke_python(env):
    """向量网络中断时先检查候选后端；不将未完整索引的版本切到正式服务。"""
    import asyncio
    import uuid
    from src.agent.v3.agent import V3Agent
    from src.etl.ocr_trial import TrialRepository
    from src.agent.v3.repository import releases
    from sqlalchemy import select
    info=current();work=ROOT/info['work'];output=work/'smoke-python';output.mkdir(exist_ok=True)
    questions=smoke_questions()
    cases_path=output/'cases.json'
    if cases_path.exists() and read(cases_path)!=[list(value) for value in questions]:
        raise ValueError('Frozen Python smoke questions changed')
    if not cases_path.exists():write(cases_path,questions)
    class CandidateRepository(TrialRepository):
        release_status='staged'
    writer,reader=engines()
    try:
        with reader.connect() as conn:status=conn.execute(select(releases.c.status).where(releases.c.version==info['version'])).scalar_one()
        factory=CandidateRepository if status=='staged' else TrialRepository
        repository=factory(reader,version=info['version'])
        companies={report['stock_code']:report['company'] for report in repository.report_catalog()}
        agent=V3Agent(reader,companies,repository_factory=lambda engine,version=None:factory(engine,version or info['version']))
        histories={};records=[]
        index_complete=(work/'index.json').is_file() and read(work/'index.json').get('accepted') is True
        async def run():
            consecutive=0
            for ordinal,(group,question) in enumerate(questions,1):
                record=output/f'{ordinal:02d}.json'
                if record.exists():value=read(record)
                else:
                    intent=output/f'{ordinal:02d}.intent.json'
                    if not intent.exists():write(intent,dict(question=question,turn_id=str(uuid.uuid4()),history=histories.get(group,[])))
                    request=read(intent);events=[];started=time.monotonic()
                    async def emit(kind,payload):events.append(dict(type=kind,data=payload))
                    try:
                        result=await agent.run(question,request['history'],request['turn_id'],time.time()+270,emit)
                        value=dict(question=question,result=result,events=events,seconds=round(time.monotonic()-started,3),
                                   entry='Python Agent direct; Java/SSE not tested in this record',index_complete=index_complete)
                    except Exception as exc:
                        value=dict(question=question,error_type=type(exc).__name__,diagnostics=getattr(exc,'diagnostics',{}),
                                   reason=cloud_failure(dict(reason=getattr(exc,'reason',None))),
                                   seconds=round(time.monotonic()-started,3),entry='Python Agent direct')
                    write(record,value)
                    print(json.dumps(dict(stage='smoke-python',position=ordinal,question=question,
                        status=(value.get('result') or {}).get('outcome') or value.get('error_type')),ensure_ascii=False),flush=True)
                result=value.get('result') or {}
                history=histories.setdefault(group,[])
                history.extend([dict(role='user',content=question),dict(role='assistant',
                    content=(result.get('answer') or {}).get('content','本轮未完成'),metadata=dict(dialogue_state=result.get('dialogue_state')))])
                records.append(value)
                reason=cloud_failure(value)
                consecutive=consecutive+1 if reason or value.get('error_type') else 0
                if reason in {'cloud_api_key_required','cloud_authentication_or_balance_failed','cloud_model_or_endpoint_not_found'} or consecutive>=2:break
        asyncio.run(run())
    finally:writer.dispose();reader.dispose()
    write(output/'summary.json',records)
    parts=['<!doctype html><meta charset="utf-8"><title>OCR试验后端问答</title><style>body{font:16px/1.7 system-ui;max-width:1100px;margin:32px auto;padding:20px}pre{white-space:pre-wrap;background:#f4f6f8;padding:18px}article{border-top:1px solid #ccc;padding:20px 0}</style>',
           '<h1>真实财报重提取：后端问答记录</h1><p>真实DeepSeek、只读SQL和云向量检索；本记录未通过Java/SSE。来源核验状态逐项保留，小样例不作为正式准确率。</p>']
    for ordinal,row in enumerate(records,1):
        result=row.get('result') or {};answer=(result.get('answer') or {}).get('content') or str(row.get('error_type'))
        parts.append(f'<article><h2>{ordinal}. {html.escape(row["question"])}</h2><pre>{html.escape(answer)}</pre>{chart_preview(result)}<details><summary>完整结果和诊断</summary><pre>{html.escape(json.dumps(row,ensure_ascii=False,indent=2))}</pre></details></article>')
    (output/'answers.html').write_text('\n'.join(parts),encoding='utf-8')
    return dict(status='python_smoke_recorded',turns=len(records),planned=len(questions),answers=str(output/'answers.html'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['prepare','index','publish','enable','smoke','smoke-python','finish','status','all'])
    args=parser.parse_args();env=environment()
    try:
        if args.stage=='all':
            prepare();index();result=publish()
        elif args.stage=='finish':
            index();publish();enable()
            from src.etl.submission_upgrade import restart,load_environment
            restart(load_environment(ROOT))
            import requests
            for attempt in range(45):
                try:
                    response=requests.get('http://127.0.0.1:18280/api/health',timeout=2)
                    if response.status_code<500:break
                except requests.RequestException:pass
                time.sleep(1)
            result=smoke(load_environment(ROOT))
        elif args.stage=='status':result=current()
        elif args.stage=='smoke-python':result=smoke_python(env)
        elif args.stage=='smoke':result=smoke(env)
        else:result=globals()[args.stage]()
        print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        return 2 if result.get('status')=='paused' or result.get('turns',0)<result.get('planned',0) else 0
    except KeyboardInterrupt:raise
    except Exception as exc:
        # 不打印HTTP请求对象、数据库连接串、私有配置等可能带认证的异常文本。
        print(json.dumps(dict(status='paused',stage=args.stage,error_type=type(exc).__name__,
                              reason=str(exc) if isinstance(exc,ValueError) else '检查已保存的进度；同一命令可续跑'),ensure_ascii=False),flush=True)
        return 2


if __name__=='__main__':raise SystemExit(main())
