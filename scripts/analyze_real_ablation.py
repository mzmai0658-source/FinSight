"""作品说明：汇总不可变真实报告回答，不再调用模型或重新评分。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

parser = argparse.ArgumentParser()
parser.add_argument('run', type=Path)
parser.add_argument('--partial', action='store_true')
args = parser.parse_args()
run = args.run
read = lambda name: json.loads((run / name).read_text(encoding='utf-8'))
manifest, status = read('report.manifest.json'), read('run_status.json')
rows = [json.loads(line) for line in (run / 'report.jsonl').read_text(encoding='utf-8').splitlines()]
if not args.partial:
    assert status['status'] in {'completed', 'completed_with_errors'} and status.get('disk_replay_verified')
    assert len(rows) == len(manifest['selected_case_ids']) * len(manifest['modes'])
keys = [(r['case_id'], r['mode']) for r in rows]
assert len(keys) == len(set(keys))
assert all(r['manifest_fingerprint'] == manifest['fingerprint'] for r in rows)
lookup = dict(zip(keys, rows))
primary = {'numeric': 'numeric_accuracy', 'chart': 'numeric_accuracy',
           'citation': 'citation_supported', 'out_of_scope': 'honest_refusal',
           'security': 'dangerous_sql_blocked'}
refusal_markers = ('暂不提供', '无法支持', '无法确认', '无法提供', '未找到', '未查询到', '没有找到',
                   '未检索到', '无法核实', '无法获取', '未获取到', '未收录', '证据不足')

def passed(r):
    return r.get('scores', {}).get(primary.get(r['category'], ''))

def disposition(r):
    if r.get('infrastructure_failure'):
        return 'infrastructure_error'
    if r.get('error') or r.get('terminal_status') != 'completed':
        return 'incomplete_or_error'
    if (r.get('result') or {}).get('needs_clarification'):
        return 'clarification'
    if r['category'] == 'out_of_scope' and passed(r) is True:
        return 'correct_refusal_candidate'
    if any(marker in (r.get('answer') or '') for marker in refusal_markers):
        return 'withheld_by_text_heuristic'
    return 'answer'

result = {'status': status, 'report_sha256': hashlib.sha256((run/'report.jsonl').read_bytes()).hexdigest(),
          'manifest_fingerprint': manifest['fingerprint'], 'modes': {}, 'paired': {}, 'failures': []}
for mode in manifest['modes']:
    group = [r for r in rows if r['mode'] == mode]
    times = [float(r['latency_seconds']) for r in group]
    metrics = {}
    for metric in sorted({k for r in group for k in r.get('scores', {})}):
        values = [r['scores'][metric] for r in group if r['scores'].get(metric) is not None]
        metrics[metric] = {'true': sum(v is True for v in values), 'denominator': len(values)}
    checks = [passed(r) for r in group if passed(r) is not None]
    calls = [c for r in group for c in r.get('llm_diagnostics', [])]
    verified = [r for r in group if (r.get('result', {}).get('verification') or {}).get('status') == 'pass']
    result['modes'][mode] = {
        'records': len(group), 'planned': len(manifest['selected_case_ids']),
        'completed': sum(r.get('terminal_status') == 'completed' and not r.get('error') for r in group),
        'errors': sum(bool(r.get('error')) for r in group),
        'annotation_missing': sum(r.get('label_status') == 'annotation_missing' for r in group),
        'primary_pass': sum(v is True for v in checks), 'primary_denominator': len(checks),
        'metrics': metrics, 'dispositions': dict(Counter(disposition(r) for r in group)),
        'latency_mean_seconds': statistics.mean(times) if times else None,
        'latency_median_seconds': statistics.median(times) if times else None,
        'latency_total_seconds': sum(times),
        'model_calls': len(calls), 'truncated_calls': sum(r.get('model_truncated_calls', 0) for r in group),
        'completion_tokens': sum((c.get('usage') or {}).get('completion_tokens', 0) for c in calls),
        'retrieval_questions': sum('search_documents' in (r.get('tools') or []) for r in group),
        'verified_pass_records': len(verified),
        'verified_pass_independently_failed': sum(passed(r) is False for r in verified),
        'latency_by_category': {category: {
            'records': sum(r['category'] == category for r in group),
            'median_seconds': statistics.median(float(r['latency_seconds']) for r in group if r['category'] == category),
            'mean_seconds': statistics.mean(float(r['latency_seconds']) for r in group if r['category'] == category),
            'truncated_calls': sum(r.get('model_truncated_calls', 0) for r in group if r['category'] == category),
        } for category in sorted({r['category'] for r in group})},
    }
    for r in group:
        if passed(r) is False or r.get('error') or r.get('label_status') == 'annotation_missing':
            result['failures'].append({'case_id': r['case_id'], 'mode': mode, 'category': r['category'],
                'primary_score': passed(r), 'label_status': r['label_status'], 'disposition': disposition(r),
                'error': r.get('error'), 'truncated_calls': r.get('model_truncated_calls', 0),
                'verification': (r.get('result', {}).get('verification') or {}).get('status')})

for other in [m for m in manifest['modes'] if m != 'agent']:
    pairs = [(lookup[(cid, 'agent')], lookup[(cid, other)]) for cid in manifest['selected_case_ids']
             if (cid, 'agent') in lookup and (cid, other) in lookup]
    comparable = [(a, b) for a, b in pairs if passed(a) is not None and passed(b) is not None]
    delta = [float(a['latency_seconds'])-float(b['latency_seconds']) for a,b in pairs]
    result['paired'][other] = {
        'same_question_pairs': len(pairs), 'scorable_pairs': len(comparable),
        'agent_only_pass': [a['case_id'] for a,b in comparable if passed(a) and not passed(b)],
        'other_only_pass': [a['case_id'] for a,b in comparable if passed(b) and not passed(a)],
        'both_pass': sum(passed(a) and passed(b) for a,b in comparable),
        'both_fail': sum(not passed(a) and not passed(b) for a,b in comparable),
        'agent_minus_other_latency_mean_seconds': statistics.mean(delta) if delta else None,
        'agent_minus_other_latency_median_seconds': statistics.median(delta) if delta else None,
        'agent_withheld_other_failed_answer': [a['case_id'] for a,b in comparable
            if passed(a) is False and passed(b) is False
            and (a.get('result', {}).get('verification') or {}).get('status') == 'fail'
            and '暂不提供未经核实的数字' in (a.get('answer') or '') and disposition(b) == 'answer'],
    }
output = Path('data/runtime/eval/holdout-partial-analysis.json') if args.partial else run/'analysis.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
for mode, item in result['modes'].items():
    print(mode, item['records'], 'primary', f"{item['primary_pass']}/{item['primary_denominator']}",
          'median', item['latency_median_seconds'], 'dispositions', item['dispositions'])
print('saved', output)
