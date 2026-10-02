"""作品说明：根据不可变对话运行记录生成可复核的前后对照报告。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics


def load_run(folder: Path):
    cases=json.loads((folder/'cases.json').read_text(encoding='utf-8'))
    report=json.loads((folder/'score.json').read_text(encoding='utf-8'))
    records=[json.loads(line) for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    snapshot=json.loads((folder/'snapshot.json').read_text(encoding='utf-8'))
    if len(cases)!=len(records) or len(cases)!=report['completed']:
        raise ValueError(f'Incomplete run: {folder}')
    case_ids=[case['id'] for case in cases]
    if (case_ids!=[record['case']['id'] for record in records]
            or case_ids!=[row['id'] for row in report['results']]):
        raise ValueError(f'Cases, records and scores are misaligned: {folder}')
    hashes={table:hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
            for table,rows in snapshot['tables'].items()}
    return cases,report,records,hashes


def describe(name,run):
    cases,score,records,_=run
    times=[float(r['elapsed']) for r in records if r.get('elapsed') is not None]
    p90=sorted(times)[min(len(times)-1,int(len(times)*.9))] if times else 0
    categories=defaultdict(list)
    for row in score['results']:
        categories[row['category']].append(row)
    lines=[f'### {name}', '',f'- 完成：{len(cases)} / {len(cases)} 轮；自动检查无失败：{score["automatic_pass"]} 轮。',
           f'- 耗时：中位数 {statistics.median(times):.1f} 秒，P90 {p90:.1f} 秒，最长 {max(times):.1f} 秒。',
           f'- 需人工复核语义：{score["review_required"]} 轮。', '',
           '| 类型 | 轮数 | 自动检查无失败 |','|---|---:|---:|']
    available=[row for row in score['results'] if row.get('available_value_case')]
    lines.insert(3,f'- 库中有请求值的轮次：{sum(row["automatic_pass"] for row in available)} / {len(available)} 轮通过自动检查。')
    for category,items in sorted(categories.items()):
        lines.append(f'| {category} | {len(items)} | {sum(i["automatic_pass"] for i in items)} |')
    lines += ['', '失败类别：']
    for reason,count in sorted(score['failure_categories'].items()):
        lines.append(f'- `{reason}`：{count} 次。')
    if not score['failure_categories']: lines.append('- 无自动检查失败。')
    failures=[f'{r["id"]} ({", ".join(r["failures"])})' for r in score['results'] if r['failures']]
    lines+=['', '失败轮次：']
    lines += [f'- {failure}' for failure in failures] if failures else ['- 无。']
    lines.append('')
    if name!='修改前':
        outcomes=Counter((r.get('result') or {}).get('outcome',{}).get('status','missing') for r in records)
        lines.append('结果状态：'+ '，'.join(f'{k} {v}' for k,v in sorted(outcomes.items()))+'。')
        origins=Counter((r.get('result') or {}).get('query_plan',{}).get('origins',{}).get('intent','missing') for r in records)
        lines.append('意图规划来源：'+ '，'.join(f'{k} {v}' for k,v in sorted(origins.items()))+'。')
    lines.append('')
    return lines


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--after',type=Path,required=True)
    parser.add_argument('--holdout',type=Path)
    parser.add_argument('--debugged-holdout',type=Path,action='append',default=[])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    before=load_run(args.before); after=load_run(args.after)
    if len(before[0])!=120 or len(after[0])!=120:
        raise ValueError('Development runs must each have all 120 rounds')
    if before[0]!=after[0]:
        raise ValueError('Development question sets differ; before/after comparison is invalid')
    if before[3]!=after[3]:
        raise ValueError('Development database snapshots differ; before/after comparison is invalid')
    if not before[1].get('scorer_sha256') or before[1]['scorer_sha256']!=after[1].get('scorer_sha256'):
        raise ValueError('Development runs must be rescored with the same scorer version')
    lines=['# 财报问答统一测试对照', '',
           '本报告比较同一批 120 轮自然语言问题。已有入库财务数据是本次问答改造的数值参照；自动检查不能替代对措辞、上下文和解释性的人工判断。', '',
           '数据库快照一致，且两轮使用同一版自动评分脚本（SHA-256：`'+before[1]['scorer_sha256'][:12]+'`）。', '']
    lines+=describe('修改前',before)+describe('修改后',after)
    for index,folder in enumerate(args.debugged_holdout,1):
        used=load_run(folder)
        if len(used[0])!=30: raise ValueError('Debugged holdout run must have 30 rounds')
        if used[3]!=after[3]: raise ValueError('Debugged holdout database snapshot differs from the development run')
        if used[1].get('scorer_sha256')!=after[1]['scorer_sha256']:
            raise ValueError('Debugged holdout must be rescored with the same scorer version')
        lines+=describe(f'早期保留测试 {index}（已用于调试）',used)
    if args.holdout:
        held=load_run(args.holdout)
        if len(held[0])!=30: raise ValueError('Holdout run must have 30 rounds')
        frozen=json.loads((args.after/'holdout-frozen.json').read_text(encoding='utf-8'))
        if frozen!=held[0]:
            raise ValueError('Holdout questions differ from those frozen before the development run')
        if held[3]!=after[3]:
            raise ValueError('Holdout database snapshot differs from the development run')
        if held[1].get('scorer_sha256')!=after[1]['scorer_sha256']:
            raise ValueError('Holdout must be scored with the same scorer version')
        lines+=describe('首次保留测试',held)
    lines+=['## 判读边界','',
            '- 自动检查核对请求范围、当前库事实、缺失和失败分类、图表事实绑定、多值表格，以及明确问句中的目录误答、年份披露、比较结论与限制、因果前提、部分可答澄清。',
            '- `automatic_pass` 不是人工语义通过率；需要逐项审阅原话、省略追问和解释是否恰当。',
            '- 任何再次修改后用过的保留问题须标成开发问题，另补新保留问题。','']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text('\n'.join(lines),encoding='utf-8')
    print(args.output)


if __name__=='__main__': main()
