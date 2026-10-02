"""作品说明：重新核验 PDF 区域后导入人工坐标标注。含糊 OCR 留在审计中；已复核单元格作为独立候选，仍须通过冲突与发布检查。"""
from functools import lru_cache
import json
from pathlib import Path
from decimal import Decimal
from src.agent.v3.catalog import METRICS
from src.agent.v3.contracts import Fact,Source,OriginalCellProof,OriginalRegion
from src.agent.v3.repository import identity_digest
from src.utils.financial_numbers import cell_decimal,normalize_cell
from src.utils.original_regions import checked_regions,checked_cell_binding,original_region_text

ROOT=Path(__file__).resolve().parents[2]
REVIEW_FILE=ROOT/'data/reference/v3/reviewed_original_cells.json'

@lru_cache(maxsize=1)
def annotations():
    return json.loads(REVIEW_FILE.read_text(encoding='utf-8'))['records']

def audited_annotations(report,digest,version):
    found=[];absence=[]
    path=ROOT/report['source_path']
    for record in annotations():
        if (record['stock_code'],record['year'],record['period'])!=(report['stock_code'],report['year'],report['period']):continue
        if record['document_version']!=digest or record['source_path']!=report['source_path']:raise ValueError('Reviewed source identity changed')
        metric=METRICS[record['metric']]
        if record['scope'] not in metric.scopes:raise ValueError('Reviewed cell scope incompatible with metric')
        if record.get('status')=='not_disclosed':
            stat=path.stat()
            for blank in record['blank_cells']:
                for region in blank.values():
                    r=OriginalRegion.model_validate(region)
                    if original_region_text(str(path),stat.st_size,stat.st_mtime_ns,r.page,r.bbox)!=r.text:raise ValueError('Blank original region changed')
                if blank['value']['text'].strip():raise ValueError('Declared blank original is not blank')
            absence.append(dict(metric=metric.id,scope=record['scope'],status='not_disclosed',
                detail='Original cells are explicitly blank in both the main indicators and consolidated income statement; no substitute used',
                source=record))
            continue
        proof=OriginalCellProof.model_validate({key:record[key] for key in ('value','row','heading','context','scope_context') if key in record})
        number=cell_decimal(proof.value.text)
        value=normalize_cell(proof.value.text,record['raw_unit'],metric.unit)
        if number is None or value is None:raise ValueError('Reviewed cell cannot normalize exactly')
        source=Source(document_id='pdf-'+digest,document_version=digest,source_sha256=digest,source_path=report['source_path'],
            page=proof.value.page,table='Original coordinate-reviewed table',row=proof.row.text,column=proof.heading.text,
            row_index=record['row_index'],column_index=1,raw_value=proof.value.text,raw_unit=record['raw_unit'],
            raw_precision=max(0,-number.as_tuple().exponent),literal=proof.row.text+' | '+proof.value.text,
            extraction='pdf_text',verification='literal_checked',original_cell=proof)
        key=dict(code=report['stock_code'],year=report['year'],period=report['period'],metric=metric.id,scope=record['scope'],document=digest)
        fact=Fact(id=identity_digest(key),data_version=version,stock_code=report['stock_code'],company=report['company'],year=report['year'],
            period=report['period'],metric=metric.id,scope=record['scope'],value=format(value,'f'),unit=metric.unit,status='verified',source=source)
        if not checked_regions(path,proof) or not checked_cell_binding(fact,proof):raise ValueError('Reviewed original coordinates/binding failed')
        factor=value/number if number else Decimal(1)
        found.append((fact,abs(factor)*Decimal(10)**-source.raw_precision,True))
    return found,absence
