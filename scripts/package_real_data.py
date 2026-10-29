"""封装未改写的真实财报、核验配套及可选OCR；本脚本不上传第三方资料。"""
import argparse,json,sys,zipfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.etl.real_delivery import digest,check_archive
from src.utils.ocr_json_parser import OCR_JSON_SUFFIXES

def package(audit,output):
    reports=json.loads((audit/'reports.json').read_text(encoding='utf-8'))
    release=json.loads((audit/'manifest.json').read_text(encoding='utf-8'))
    if len(reports)!=117 or len({r['stock_code'] for r in reports})!=10:raise ValueError('Expected the real 117-report cohort')
    output.mkdir(parents=True,exist_ok=True)
    notice=('FinSight真实资料附件\n\n117份上市公司真实财报，保留原始文件字节。来源为公司公开披露的财报，文件指纹及身份见清单。'
            '报告及其中商标、文字的权利归各权利人；FinSight代码许可不覆盖第三方财报。'
            'OCR仅为原件的机器识别缓存，不代表全文识别正确或全文人工审核。'
            '本地准备不代表已获得公共再分发许可，不代表已上传云盘。公开分享范围及具体原始披露链接仍需核实。\n')
    inputs={'originals':{},'assets':{},'ocr':{}}
    for row in reports:
        path=ROOT/row['source_path']
        if digest(path)!=row['document_version']:raise ValueError('Original file changed')
        inputs['originals'][row['source_path']]=path
        caches=[Path(str(path)+suffix) for suffix in OCR_JSON_SUFFIXES if Path(str(path)+suffix).is_file() and digest(Path(str(path)+suffix))==row['cache_sha256']]
        if len(caches)!=1:raise ValueError('Accepted OCR cache missing or ambiguous')
        cache=caches[0];relative=cache.relative_to(ROOT).as_posix();inputs['ocr'][relative]=cache
        sidecar=Path(str(cache)+'.meta.json')
        if sidecar.exists():
            raw_metadata=json.loads(sidecar.read_text(encoding='utf-8'))
            metadata={k:raw_metadata[k] for k in ('schema_version','pdf_sha256','cache_sha256','model','origin','created_at','content_review_status') if k in raw_metadata}
        else:metadata=dict(schema_version=1,pdf_sha256=row['document_version'],cache_sha256=row['cache_sha256'],origin='accepted_manifest_export_binding',generation_time_binding='unknown',content_review_status='full_ocr_not_manually_reviewed')
        if metadata['pdf_sha256']!=row['document_version'] or metadata['cache_sha256']!=row['cache_sha256']:raise ValueError('OCR sidecar changed')
        inputs['ocr'][relative+'.meta.json']=json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8')
    for name in ('facts.json','reports.json','narratives.jsonl','manifest.json','index_coverage.json','acceptance.json'):
        inputs['assets']['data/runtime/real-package/'+name]=audit/name
    for name in ('reviewed_original_cells.json','statement_scope_proofs.json'):
        inputs['assets']['data/reference/v3/'+name]=ROOT/'data/reference/v3'/name
    inventory=json.dumps([dict(stock_code=r['stock_code'],company=r['company'],year=r['year'],period=r['period'],source_path=r['source_path'],document_version=r['document_version'],page_count=r['page_count'],cache_sha256=r['cache_sha256']) for r in reports],ensure_ascii=False,indent=2).encode('utf-8')
    inputs['originals']['data/runtime/real-package/ORIGINAL_INVENTORY.json']=inventory
    for kind in inputs:inputs[kind]['docs/data-notices/'+kind+'.txt']=notice.encode('utf-8')
    bundles=[]
    for kind,files in inputs.items():
        target=output/f'FinSight-real-{kind}.zip'
        if target.exists():raise FileExistsError('Use a new version directory; do not overwrite delivered archives')
        rows=[]
        for name,value in sorted(files.items()):
            raw=value.read_bytes() if isinstance(value,Path) else value
            rows.append(dict(path=name,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
        manifest=dict(schema=1,dataset='real-117-v1',kind=kind,data_version=release['version'],reports=117,companies=10,files=rows)
        temporary=target.with_suffix('.zip.partial')
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for name,value in sorted(files.items()):
                if isinstance(value,Path):archive.write(value,name)
                else:archive.writestr(name,value)
            archive.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        temporary.replace(target);check_archive(target)
        row=dict(kind=kind,file=target.name,sha256=digest(target),bytes=target.stat().st_size,files=len(rows));bundles.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    spec=dict(schema=1,dataset='real-117-v1',data_version=release['version'],packages=bundles,uploaded=False,download_verified=False,redistribution_status='pending source-specific review',code_binding='record in final source manifest; assets acceptance checks extractor dependencies')
    (output/'PACKAGE_SET.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'SHA256SUMS.txt').write_text(''.join(r['sha256']+'  '+r['file']+'\n' for r in bundles),encoding='utf-8')
    return spec

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--audit',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();package(a.audit.resolve(),a.output.resolve())
