"""作品说明：对固定选中的真实报告运行生产 PDF 导入链路。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',default='.env.real-eval')
    parser.add_argument('--manifest',type=Path,default=ROOT/'data/runtime/real_validation/report_manifest.json')
    parser.add_argument('--split',choices=['development','prospective_holdout','all'],default='development')
    parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--report-period',choices=['FY','HY','Q1','Q3'])
    parser.add_argument('--ocr-cache-dir',type=Path,help='Use SHA-validated new OCR results for the same frozen originals')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from dotenv import dotenv_values
    os.environ.update({k:v for k,v in dotenv_values(args.env_file).items() if v is not None})
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    os.environ['HF_HUB_OFFLINE']='1'
    from config.db_config import get_db_config
    config=get_db_config()
    if config.database!='finsight_real_eval' or config.host not in {'localhost','127.0.0.1','::1'}:
        raise RuntimeError('Real validation import must use the dedicated local database')
    if args.output.exists():raise FileExistsError('Refusing to overwrite ingestion evidence')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    selected=[r for r in json.loads(args.manifest.read_text(encoding='utf-8'))['documents'] if args.split=='all' or r['split']==args.split]
    if args.report_period:selected=[r for r in selected if r['report_period']==args.report_period]
    if args.limit:selected=selected[:args.limit]
    from src.etl.pipeline import run_single_file
    from src.agent.llm_client import LLMClient
    class AuditedLLM(LLMClient):
        def __init__(self):
            super().__init__()
            self.calls=[]

        def _request_completion(self, payload):
            call={'request':{**payload, **self._reasoning_options()}}
            self.calls.append(call)
            started=time.monotonic()
            try:
                result=super()._request_completion(payload)
                call['response']=result
                return result
            finally:
                call['elapsed_seconds']=round(time.monotonic()-started,3)

    llm=AuditedLLM()
    from urllib.parse import urlparse
    if llm.provider!='ollama' or llm.model!='qwen3.5:9b-q4_K_M' or llm.reasoning_effort!='low' or urlparse(llm.api_url).hostname not in {'localhost','127.0.0.1','::1'}:
        raise RuntimeError('This experiment requires the local thinking model')
    records=[]
    def checkpoint(status, **extra):
        temporary=args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps({'planned':len(selected),'completed':len(records),
            'status':status,'model':llm.model,'reasoning_effort':llm.reasoning_effort,
            'selection':selected,'records':records,**extra},ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(args.output)
    checkpoint('running')
    for index,document in enumerate(selected,1):
        started=time.monotonic()
        pdf=ROOT/document['source_path']
        with pdf.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=document['source_sha256']:
                raise RuntimeError('Frozen original changed')
        print(f'IMPORT {index}/{len(selected)} {document["stock_code"]} {document["report_year"]} {document["report_period"]}',flush=True)
        llm.calls=[]
        try:
            import requests
            parsed=urlparse(llm.api_url)
            requests.get(f'{parsed.scheme}://{parsed.netloc}/api/version',timeout=5).raise_for_status()
            if args.ocr_cache_dir:
                from src.etl.ocr_client import write_validated_ocr_cache
                cached=args.ocr_cache_dir/(pdf.name+'_by_PaddleOCR-VL-1.6.json')
                encoded=cached.read_bytes()
                metadata=json.loads(Path(str(cached)+'.meta.json').read_text(encoding='utf-8'))
                if metadata['pdf_sha256']!=document['source_sha256'] or metadata['cache_sha256']!=hashlib.sha256(encoded).hexdigest() or metadata['model']!='PaddleOCR-VL-1.6':
                    raise RuntimeError('New OCR result does not match frozen PDF or metadata')
                write_validated_ocr_cache(pdf,json.loads(encoded),model=metadata['model'],origin=metadata['origin'],expected_pdf_sha256=document['source_sha256'])
            report=run_single_file(pdf,llm_client=llm).to_dict()
        except Exception as exc:report={'status':'failed','message':f'{type(exc).__name__}: {exc}'}
        record={'document':document,'report':report,'llm_calls':llm.calls,'elapsed_seconds':round(time.monotonic()-started,3)}
        records.append(record)
        checkpoint('running')
        print('IMPORTED',report.get('status'),report.get('message'),flush=True)
        if report.get('status')=='failed':
            # 作品说明：失败报告保留在统计分母中；服务不可用时应及时终止，避免长时间运行只有兜底结果的失真实验。
            import requests
            try:requests.get('http://127.0.0.1:11434/api/version',timeout=5).raise_for_status()
            except requests.RequestException:
                checkpoint('aborted_service_unavailable')
                raise RuntimeError('Local model service stopped; import checkpoint retained')
    checkpoint('completed')
    print(f'IMPORT COMPLETE {len(records)} records; {sum(r["report"].get("status")=="success" for r in records)} successful',flush=True)


if __name__=='__main__':main()
