"""管理员财报上传只在规范事实及索引验收发布后宣布可用于Agent。"""
from __future__ import annotations
import json,shutil,time
from pathlib import Path
from sqlalchemy import create_engine,select
from src.etl.real_delivery import ROOT,digest,register_reports,run_stage,publish_candidate
from src.etl.pipeline import PipelineReport

def run_canonical_file(pdf_path,ingest_rag=True):
    report=PipelineReport(file=str(pdf_path));started=time.monotonic()
    try:
        from config.db_config import get_db_config
        from src.agent.v3.repository import reports,releases,pointer,json_payload
        from src.etl.ocr_client import ensure_ocr_json
        from src.utils.report_identity import original_report_identity
        from pypdf import PdfReader
        path=Path(pdf_path).resolve()
        if not path.is_relative_to(ROOT/'data_root') or path.suffix.lower()!='.pdf':raise ValueError('上传原件必须位于data_root')
        engine=create_engine(get_db_config().connection_string)
        with engine.connect() as conn:
            version=conn.execute(select(pointer.c.version).where(pointer.c.name=='serving')).scalar_one()
            release=conn.execute(select(releases).where(releases.c.version==version)).mappings().one()
            if release['status']!='accepted':raise ValueError('当前版本未验收，先初始化真实资料')
            cohort=[json_payload(p) for p in conn.execute(select(reports.c.payload).where(reports.c.data_version==version)).scalars()]
        sha=digest(path)
        duplicate=next((r for r in cohort if r['document_version']==sha),None)
        if duplicate:
            if digest(ROOT/duplicate['source_path'])!=sha:raise ValueError('已发布原件损坏，不能以上传成功掩盖')
            manifest=json_payload(release['manifest'])
            if not all(manifest.get('acceptance',{}).get(k) for k in ('facts_verified','reports_verified','index_verified','projections_verified')):raise ValueError('发布记录没有完整核验')
            from src.agent.v3.evidence import NarrativeRetriever
            retriever=NarrativeRetriever(release['index_collection'])
            if not retriever.collection.get(where={'document_version':sha},limit=1)['ids']:raise ValueError('现用索引缺少本原件')
            report.stock_code=duplicate['stock_code'];report.stock_abbr=duplicate['company'];report.report_year=duplicate['year'];report.report_period=duplicate['period']
            report.add_step('canonical_identity','ok','原件内容摘要与当前v3登记一致')
            report.add_step('canonical_publish','ok','同内容不重复抽取或入库；已验收版本 '+version)
            report.status='success';report.message='真实原件已在当前v3事实及索引中可用（内容去重）'
            return report
        if not ingest_rag:raise ValueError('v3发布需要事实和索引共同验收，不能关闭索引后宣称可用')
        reader=PdfReader(path);front='\n'.join(p.extract_text() or '' for p in reader.pages[:10])
        matches=[r for r in cohort if all(original_report_identity(r,front)[k] for k in ('company','period'))]
        if len(matches)!=1:raise ValueError('原件不属于唯一已覆盖公司/报告期；本轮不扩大范围，请先核对登记及原件身份')
        selected=matches[0]
        report.stock_code=selected['stock_code'];report.stock_abbr=selected['company'];report.report_year=selected['year'];report.report_period=selected['period']
        canonical=ROOT/'data_root/financial_reports/uploads'/f'{sha}.pdf';canonical.parent.mkdir(parents=True,exist_ok=True)
        if not canonical.exists():shutil.copyfile(path,canonical)
        cache=ensure_ocr_json(path)
        target_cache=Path(str(canonical)+Path(cache).name[len(path.name):])
        if not target_cache.exists():shutil.copyfile(cache,target_cache)
        sidecar=Path(str(cache)+'.meta.json')
        if sidecar.exists():shutil.copyfile(sidecar,Path(str(target_cache)+'.meta.json'))
        replacement={**selected,'source_path':canonical.relative_to(ROOT).as_posix(),'document_version':sha,'page_count':len(reader.pages)}
        register_reports(engine,[replacement]);report.add_step('canonical_registration','ok','候选文件身份已登记，尚未发布')
        folder=ROOT/'data/runtime/upload-candidates'/sha
        run_stage('audit_canonical_facts.py',ROOT/'.env',folder)
        report.add_step('canonical_extract','ok','从原件和OCR重新抽取，来源口径未确认的单元格保持排除')
        run_stage('build_canonical_index.py',ROOT/'.env',folder)
        report.add_step('canonical_index','ok','候选正文索引已生成')
        receipt=publish_candidate(engine,folder)
        report.add_step('canonical_publish','ok',json.dumps(receipt,ensure_ascii=False))
        report.status='success';report.message='v3事实与索引已验收并原子发布'
    except Exception as exc:
        report.add_step('canonical_publish','failed',str(exc),int((time.monotonic()-started)*1000))
        report.status='failed';report.message='v3候选未完成发布；当前已验收版本继续服务。'+str(exc)
    return report
