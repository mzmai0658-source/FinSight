"""补充核对比較结论、计算正文与缺失值，重评原始记录，不更改原问题或预期。"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal,ROUND_HALF_UP,localcontext
from pathlib import Path

from eval.common300_scoring import score_case,effective,expected_facts,match_goals,validated_visible_context
from eval.common300_run import export
from eval.acceptance_scoring import recorded_model
from eval.real200_acceptance import FACTORS,reference,wanted


def strict_score(case,run,ref,oracle,model,actual_context=(),expected_context=()):
    verdict=score_case(case,run,ref,oracle,model,actual_context,expected_context)
    result=(run.get('task') or {}).get('result') or {}
    body=(result.get('answer') or {}).get('content','');facts=result.get('facts') or [];indexed={f['id']:f for f in facts}
    errors=list(verdict['errors']);critical=list(verdict['critical'])
    def require(ok,label,severe=False):
        if not ok:errors.append(label)
        if not ok and severe:critical.append(label)
    e=case['expect'];specs=e['goal_expectations'];contract=result.get('request_contract') or {}
    matched=match_goals(specs,contract)
    for spec,(observed,_) in zip(specs,matched):
        if not observed or spec.get('completion')=='missing_report':continue
        allowed={f['id'] for f in wanted({**spec,'goals':[spec['kind']]},ref)} if spec['kind'] in {'compare','lookup','rank','chart','cause','quote','sign'} else set()
        if spec['kind']=='compare':
            comparisons=[v for v in result.get('comparisons') or [] if {v.get('first'),v.get('second')}<=allowed]
            require(bool(comparisons),'comparison_conclusion_missing')
            for v in comparisons:
                a,b=indexed.get(v.get('first')),indexed.get(v.get('second'));correct=bool(a and b)
                if correct:
                    x,y=Decimal(a['value_exact']),Decimal(b['value_exact'])
                    correct=v.get('relation')==('greater' if x>y else 'less' if x<y else 'equal')
                    correct=correct and all(a.get(k)==b.get(k) for k in ('metric','scope','period','unit','data_version'))
                    correct=correct and (a.get('year')==b.get('year') if v.get('axis')=='companies' else a.get('stock_code')==b.get('stock_code'))
                require(correct,'wrong_comparison_conclusion',True)
            require(any(t in body for t in ('高于','低于','相同','相等','大于','小于','更多','更少','更高','更低','相差')),'comparison_conclusion_not_delivered')
            if spec.get('unit') and not spec.get('calculation') and not e.get('no_repeat'):
                for f in facts:
                    if f['id'] not in allowed:continue
                    unit=spec['unit'];digits=spec.get('decimals',2)
                    with localcontext() as ctx:
                        ctx.prec=50;number=(Decimal(f['value_exact'])/(FACTORS[unit] if f['unit']=='元' else 1)).quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
                    require(format(number,'f') in body.replace(',',''),'comparison_requested_number_missing')
                require(spec['unit'] in body,'comparison_requested_unit_missing')
        if spec.get('calculation'):
            derived=[d for d in result.get('derived_facts') or [] if set(d.get('inputs') or [])<=allowed]
            for d in derived:
                if d.get('value') is None:
                    require(any(t in body for t in ('未定义','无定义','不能计算')),'undefined_calculation_not_explained')
                    continue
                unit=spec.get('unit') if d.get('unit')=='元' and spec.get('unit') else '万元' if d.get('unit')=='元' else d.get('unit')
                with localcontext() as ctx:
                    ctx.prec=50;number=(Decimal(d['value'])/(FACTORS[unit] if d.get('unit')=='元' else 1)).quantize(Decimal(1).scaleb(-spec.get('decimals',2)),rounding=ROUND_HALF_UP)
                require(format(number,'f') in body.replace(',',''),'derived_number_display_missing')
                require(('个百分点' if unit=='百分点' else unit) in body,'derived_unit_display_missing')
    verdict.update(errors=sorted(set(errors)),critical=sorted(set(critical)),automated_pass=not errors,
        passed=not errors and not verdict['review_required'],scorer='common300_strict; unchanged case expectations')
    return verdict


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--audit',required=True);p.add_argument('--oracle',required=True)
    p.add_argument('--model',help='Must match the model identity in the saved run');a=p.parse_args()
    folder=Path(a.run).resolve();ref=reference(Path(a.audit).resolve());oracle=json.loads(Path(a.oracle).read_text(encoding='utf-8'))
    rows=[json.loads(line) for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    model=recorded_model(folder,rows,a.model)
    actual={};expected={};rescored=[]
    for row in rows:
        case=row['case'];g=case.get('group');run=row.get('run')
        if run:
            row['score']=strict_score(case,run,ref,oracle,model,actual.get(g,[]),expected.get(g,[]))
            if g:actual[g]=validated_visible_context(run,ref,oracle,actual.get(g,[]))
        if g:
            fresh=expected_facts(case,ref)
            if fresh:expected[g]=[{**f,'value_exact':oracle['cards'][f['id']]['value_exact']} for f in fresh.values()]
            elif case['expect'].get('clear_company') or case['expect']['kind'] in {'clarify','missing'}:expected[g]=[]
        row['scoring_history']='Raw result unchanged; stricter independent comparison/body checks added'
        rescored.append(row)
    output=folder/'strict_records.jsonl';assert not output.exists(),'Preserve previous scoring evidence'
    output.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rescored)+'\n',encoding='utf-8')
    suite=json.loads((folder/'cases.json').read_text(encoding='utf-8'))
    dest=folder/'strict-results';dest.mkdir(exist_ok=False)
    summary=export(dest,rescored,len(suite['cases']))
    print(json.dumps(dict(total=summary['total'],automatic=summary['automated_pass'],pending=summary['source_review_pending'],critical=summary['critical']),ensure_ascii=False))


if __name__=='__main__':main()
