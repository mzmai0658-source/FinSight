"""作品说明：生成可逐页回查的虚构PDF及坐标事实，不读取真实原件或模型输出。"""
from __future__ import annotations
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def generator_digest():
    paths = ('demo/v3/generate.py','demo/v3/spec.json','src/etl/release_profiles.py',
             'src/agent/v3/catalog.py','src/agent/v3/contracts.py','src/agent/v3/evidence.py',
             'src/utils/original_regions.py','src/utils/statement_scope.py',
             'src/utils/financial_row_identity.py','src/utils/financial_numbers.py',
             'src/utils/disclosed_rates.py','src/utils/original_cells.py',
             'src/utils/report_identity.py','src/etl/canonical_projection.py')
    return hashlib.sha256(b''.join((ROOT/p).read_bytes() for p in paths)).hexdigest()


def generate(output=None):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from src.agent.v3.catalog import METRICS
    from src.agent.v3.contracts import Fact, Source, OriginalRegion, OriginalCellProof, StatementScopeProof, HashedOriginalRegion
    from src.agent.v3.repository import identity_digest
    from src.agent.v3.evidence import checked_source
    from src.utils.report_identity import original_report_identity
    from src.utils.original_regions import original_region_text
    from src.etl.release_profiles import DEMO, check_report_coverage
    from pypdf import PdfReader

    folder = Path(output or ROOT/'data/runtime/demo-v3').resolve()
    if not folder.is_relative_to(ROOT): raise ValueError('Demo output must stay inside the workspace')
    pdf_root=ROOT/'data_root/synthetic_demo_v3';pdf_root.mkdir(parents=True,exist_ok=True)
    folder.mkdir(parents=True,exist_ok=True)
    spec=json.loads((ROOT/'demo/v3/spec.json').read_text(encoding='utf-8'))
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    assembled,reports,narratives=[],[],[]
    dependencies=generator_digest()
    for ci,company in enumerate(spec['companies']):
        for yi,year in enumerate(spec['years']):
            code,name=company['code'],company['name']
            path=pdf_root/f'{code}-{year}-FY.pdf'
            pdf=canvas.Canvas(str(path),pagesize=(595,842),invariant=1)
            pdf.setTitle(f'{name}{year}年度报告（虚构演示数据）');pdf.setAuthor('FinSight')
            regions=[];page=1
            def text(value,x,top,size=11):
                pdf.setFont('STSong-Light',size);pdf.drawString(x,842-top,value)
            def box(top,left=44,right=548):return (left,top-14,right,top+5)
            def table(title,family,scope,rows,top):
                caption_box=box(top);text(title,44,top,14)
                context_top=top+23;text('单位：万元；百分比按%；每股按元/股。',44,context_top)
                heading_top=top+47;heading=f'{year}年度本期金额'
                text('项目',44,heading_top);text(heading,395,heading_top)
                for index,(metric,value,unit) in enumerate(rows):
                    row_top=heading_top+25*(index+1)
                    label='净利润' if metric=='net_profit' else METRICS[metric].label
                    if unit in ('%','元/股'):label+=f'（{unit}）'
                    raw=format(value,'.2f');text(label,44,row_top);text(raw,395,row_top)
                    regions.append(dict(metric=metric,scope=scope,family=family,unit=unit,raw=raw,page=page,
                        caption=caption_box,row=box(row_top,44,384),value=box(row_top,390,548),
                        heading=box(heading_top,390,548),context=(44,top-17,548,context_top+5),row_index=index))
                return heading_top+25*len(rows)+34
            revenue=Decimal(str(company['revenue'][yi]));attr=Decimal(str(company['attributable_profit'][yi]))
            ratio=Decimal(spec['construction']['parent_ratio']);gross=Decimal(30+ci+yi);cost=revenue*(1-gross/100)
            text('虚构演示数据 · Apache-2.0 · 非上市公司披露',44,35,10)
            text(f'{name} {year}年年度报告',44,67,18);text(f'证券代码：{code}；报告期：{year}年度（全年）。',44,94)
            top=table('合并利润表','利润表','consolidated',[
                ('operating_revenue',revenue,'万元'),('total_operating_revenue',revenue+Decimal(spec['construction']['total_revenue_extra']),'万元'),
                ('operating_cost',cost,'万元'),('net_profit',attr+Decimal(spec['construction']['minority_profit']),'万元'),
                ('attributable_net_profit',attr,'万元'),('deducted_attributable_net_profit',attr-Decimal(spec['construction']['nonrecurring_profit']),'万元')],125)
            top=table('合并资产负债表','资产负债表','consolidated',[
                ('total_assets',revenue*2,'万元'),('total_liabilities',revenue,'万元'),('total_equity',revenue,'万元')],top)
            table('合并现金流量表','现金流量表','consolidated',[('operating_cash_flow',attr+Decimal('200'),'万元')],top)
            text('第1页 · 所有金额与经营情节均为教学构造。',44,813,9)
            pdf.showPage();page=2;text('虚构演示数据 · 不构成真实公司的财务信息',44,35,10)
            top=table('母公司利润表','利润表','parent',[
                ('operating_revenue',revenue*ratio,'万元'),('total_operating_revenue',revenue*ratio+Decimal('30'),'万元'),('net_profit',attr*ratio,'万元')],75)
            top=table('主要会计数据和财务指标','indicators','consolidated',[
                ('eps_basic',attr/1000,'元/股'),('roe_weighted',Decimal([5,6,8][yi]+ci),'%'),('gross_margin',gross,'%')],top)
            text('经营情况说明（虚构场景）',44,top,14)
            reason=f'{name}{year}年全年营业收入及盈利变化的主要原因是{company["reasons"][yi]}。'
            for index,start in enumerate(range(0,len(reason),40)):text(reason[start:start+40],44,top+26+22*index)
            text('以上情节为演示设定，不能推断真实公司的经营情况。',44,top+91)
            text('第2页 · 合并、归母、扣非及母公司数字具有不同身份。',44,813,9);pdf.save()
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            report=dict(stock_code=code,company=name,year=year,period='FY',source_path=path.relative_to(ROOT).as_posix(),document_version=digest,page_count=2,missing=[])
            reader=PdfReader(path);report['identity']=original_report_identity(report,reader.pages[0].extract_text());reports.append(report)
            stat=path.stat()
            def region(p,bbox):return OriginalRegion(page=p,bbox=bbox,text=original_region_text(str(path),stat.st_size,stat.st_mtime_ns,p,tuple(bbox)))
            for row in regions:
                p=row['page'];proof=OriginalCellProof(value=region(p,row['value']),row=region(p,row['row']),heading=region(p,row['heading']),context=region(p,row['context']))
                caption=region(p,row['caption'])
                def hashed(r):return HashedOriginalRegion(page=r.page,bbox=r.bbox,text_sha256=hashlib.sha256(r.text.encode()).hexdigest())
                scope=StatementScopeProof(caption=caption,family=row['family'],value_region=hashed(proof.value),label_region=hashed(proof.row))
                source=Source(document_id='pdf-'+digest,document_version=digest,source_sha256=digest,source_path=report['source_path'],
                    page=p,table=caption.text,row=proof.row.text,column=proof.heading.text,row_index=row['row_index'],column_index=1,
                    raw_value=proof.value.text,raw_unit=row['unit'],raw_precision=2,literal=proof.row.text+' | '+proof.value.text,
                    extraction='pdf_text',verification='literal_checked',original_cell=proof,scope_proof=scope)
                value=Decimal(row['raw'])*(10000 if row['unit']=='万元' else 1)
                fact=Fact(id=identity_digest(dict(document=digest,page=p,metric=row['metric'],scope=row['scope'])),data_version='pending',
                    stock_code=code,company=name,year=year,period='FY',metric=row['metric'],scope=row['scope'],value=format(value,'f'),
                    unit=METRICS[row['metric']].unit,status='verified',source=source)
                if not checked_source(fact):raise ValueError(f'Original check failed: {code}/{year}/{row["metric"]}/{row["scope"]}')
                assembled.append(fact)
            for number,p in enumerate(reader.pages,1):narratives.append(dict(stock_code=code,company=name,year=year,period='FY',document_version=digest,source_path=report['source_path'],page=number,text=p.extract_text()))
    version=identity_digest({'extractor':dependencies,'documents':sorted(r['document_version'] for r in reports)})
    facts=[f.model_copy(update={'data_version':version}) for f in assembled]
    manifest=dict(version=version,extractor_sha256=dependencies,reports=len(reports),dataset_profile=DEMO.public_metadata(),errors=[],conflicts=[],synthetic=True,license='Apache-2.0')
    check_report_coverage(manifest,reports)
    for name,value in [('manifest',manifest),('reports',reports),('facts',[f.model_dump(mode='json') for f in facts])]:
        (folder/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'narratives.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in narratives),encoding='utf-8')
    return dict(version=version,reports=len(reports),facts=len(facts),folder=str(folder))


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT));parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output')
    print(json.dumps(generate(parser.parse_args().output),ensure_ascii=False))
