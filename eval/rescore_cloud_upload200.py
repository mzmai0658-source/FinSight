"""Offline, non-mutating recalibration of frozen first answers and original PDFs.

Example::

    python -m eval.rescore_cloud_upload200 --input-run OLD --candidate-artifacts RELEASE --output NEW

This module never constructs a client or RunLedger, and never generates answers.
Every before score is copied verbatim beside its calibrated after score.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import json
import time

from eval import cloud_upload200 as harness


def _manifest(folder):
    return {path.relative_to(folder).as_posix(): harness.digest(path)
            for path in sorted(folder.rglob('*')) if path.is_file()}


def _first_results(folder):
    freeze = harness.read(folder/'freeze.json'); questions = harness.read(folder/'questions.freeze.json')
    cases = questions['cases']; fingerprint = freeze['fingerprint']
    if (freeze['cases_sha256'] != harness.value_digest(cases)
            or any(questions.get(key) != fingerprint.get(key) for key in ('suite_sha256', 'questions_sha256'))
            or len({case['id'] for case in cases}) != len(cases)):
        raise harness.FreezeMismatch('Frozen question/expectation definition changed')
    records = [json.loads(line) for line in (folder/'records.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if len(records) != len(cases):
        raise harness.FreezeMismatch('Frozen first-answer records are incomplete')
    rows = []
    for case, record in zip(cases, records):
        identifier = case['id']
        if not harness.re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
            raise harness.FreezeMismatch('Unsafe frozen case identifier')
        answer = harness.read(folder/'answers'/(identifier+'.json')); raw = answer.get('run')
        score = harness.read(folder/'scores'/(identifier+'.json'))
        if (answer.get('case') != case or answer.get('sha256') != harness.value_digest(dict(case=case, run=raw))
                or score.get('answer_sha256') != harness.value_digest(raw)
                or score.get('sha256') != harness.value_digest(score.get('score'))
                or record.get('case') != case or record.get('run') != raw
                or record.get('answer_sha256') != harness.value_digest(raw)):
            raise harness.FreezeMismatch('Frozen first answer or score binding changed: '+identifier)
        intent = harness.read(folder/'requests'/(identifier+'.json'))
        if intent.get('case') != case:
            raise harness.FreezeMismatch('Frozen request identity changed: '+identifier)
        history = harness.read(folder/'history'/(identifier+'.json'))
        rows.append(dict(case=case, run=raw, before=score['score'],
                         history_errors=harness.saved_history_errors(case, raw, history, intent['before'])))
    return cases, rows, fingerprint


def _summary(scores):
    statuses = Counter(score['status'] for score in scores)
    return dict(total=len(scores), passed=sum(score.get('passed') is True for score in scores),
                failed=statuses['failed'], oracle_missing=statuses['oracle_missing'], pending_review=statuses['pending_review'],
                automated_pass=sum(score.get('automated_pass') is True for score in scores), status_counts=dict(statuses))


def _history_score(score, errors):
    score['errors'] = sorted(set(score['errors']+errors))
    if errors:
        score.update(passed=False, automated_pass=False, status='failed')
    score['server_saved_history_checked'] = not errors
    return score


def rescore(input_run, candidate_artifacts, output, *, root=harness.ROOT):
    """Re-read registered originals and score saved answers into an exclusive new folder."""
    input_run = Path(input_run).resolve(); output = Path(output).resolve(); root = Path(root).resolve()
    if input_run == output or output.is_relative_to(input_run) or input_run.is_relative_to(output):
        raise ValueError('Rescore output must not overlap the immutable input run')
    if output.exists():
        raise FileExistsError('Offline rescore requires a new output directory')
    started = time.monotonic(); initial = _manifest(input_run)
    cases, rows, fingerprint = _first_results(input_run)
    reference = harness.load_release(candidate_artifacts, root=root, require_cohort=False)
    if (reference['version'] != fingerprint['data_version'] or reference['artifacts'] != fingerprint['artifacts']
            or reference['documents'] != fingerprint['originals']):
        raise harness.FreezeMismatch('Candidate release or registered original bytes differ from the frozen run')
    model_version = fingerprint['model']['version']; old_oracle = harness.read(input_run/'oracle.json')
    output.mkdir(parents=True)
    harness.write_new(output/'input-manifest.json', dict(input_run=str(input_run), files=initial,
        frozen_fingerprint=fingerprint, reference_sha256=harness._oracle_reference_digest(reference),
        scorer_sha256=harness.digest(Path(harness.__file__)), rescorer_sha256=harness.digest(Path(__file__))))
    oracle = harness.build_oracle(cases, reference, root=root, record_data_gaps=True)
    harness.write_new(output/'oracle.json', oracle)
    harness.write_new(output/'preparation.json', harness.preparation_summary(cases, reference, oracle))
    comparisons, old_oracle_scores = [], []
    for row in rows:
        case = row['case']; raw = row['run']; before = row['before']
        after = _history_score(harness.score_case(case, raw, reference, oracle, model_version=model_version), row['history_errors'])
        same_oracle = _history_score(harness.score_case(case, raw, reference, old_oracle, model_version=model_version), row['history_errors'])
        old_oracle_scores.append(same_oracle)
        comparison = dict(case_id=case['id'], partition=case['partition'], group=case.get('group'),
            question_sha256=harness.value_digest(case['question']), expected_sha256=harness.value_digest(case['expect']),
            raw_answer_sha256=harness.value_digest(raw), before=before, after=after,
            calibrated_with_original_oracle=same_oracle,
            rationale=dict(removed_errors=sorted(set(before['errors'])-set(after['errors'])),
                added_errors=sorted(set(after['errors'])-set(before['errors'])),
                original_missing_cards_revalidated=sorted(set(before.get('oracle_missing', [])) & set(oracle['cards'])),
                review_preserved=after.get('review_required', []), score_only_change=True, no_answer_regenerated=True))
        comparisons.append(comparison)
        harness.write_new(output/'comparisons'/(case['id']+'.json'), comparison)
    final = _manifest(input_run)
    harness.write_new(output/'input-integrity.json', dict(before=initial, after=final, unchanged=initial == final))
    if final != initial:
        raise harness.FreezeMismatch('Immutable original evaluation changed during offline rescore')
    before_scores = [row['before'] for row in rows]; after_scores = [row['after'] for row in comparisons]
    changed = [row for row in comparisons if (row['before']['status'], row['before'].get('passed'), row['before']['errors'])
               != (row['after']['status'], row['after'].get('passed'), row['after']['errors'])]
    summary = dict(schema=1, input_run=str(input_run), output=str(output), data_version=reference['version'],
        model_version_of_saved_answers=model_version, before=_summary(before_scores), after=_summary(after_scores),
        calibrated_with_original_oracle=_summary(old_oracle_scores),
        by_partition={partition: dict(before=_summary([r['before'] for r in comparisons if r['partition'] == partition]),
            after=_summary([r['after'] for r in comparisons if r['partition'] == partition]))
            for partition in sorted({case['partition'] for case in cases})},
        changed_case_ids=[row['case_id'] for row in changed],
        newly_passed_case_ids=[row['case_id'] for row in comparisons if row['after']['passed'] and not row['before']['passed']],
        lost_pass_case_ids=[row['case_id'] for row in comparisons if row['before']['passed'] and not row['after']['passed']],
        revalidated_card_ids=sorted(set(oracle['cards'])-set(old_oracle['cards'])),
        old_oracle_gaps=dict(Counter(gap['reason'] for gap in old_oracle.get('data_gaps', []))),
        calibrated_oracle_gaps=dict(Counter(gap['reason'] for gap in oracle.get('data_gaps', []))),
        no_model_call_made=True, no_network_generation=True, score_only_change=True,
        original_run_unchanged=True, original_file_count=len(initial), elapsed_seconds=round(time.monotonic()-started, 3),
        interpretation='Scoring/original-verification calibration of identical saved answers; not a model improvement.',
        review_policy=oracle.get('review_policy'))
    harness.write_new(output/'summary.json', summary)
    report = ('# Offline score calibration\n\n'+summary['interpretation']+'\n\n'
        f"Original: {summary['before']['passed']} passed, {summary['before']['failed']} failed, "
        f"{summary['before']['oracle_missing']} oracle missing, {summary['before']['pending_review']} pending review.\n\n"
        f"Calibrated: {summary['after']['passed']} passed, {summary['after']['failed']} failed, "
        f"{summary['after']['oracle_missing']} oracle missing, {summary['after']['pending_review']} pending review.\n\n"
        f"Revalidated original fact cards: {len(summary['revalidated_card_ids'])}. "
        f"Original input files verified unchanged: {len(initial)}. No answer regeneration or API calls.\n\n"
        'Per-case before/after scores and removed/added requirements are in comparisons/. '
        'Pending prose reviews remain pending; missing data and incomplete source audits remain gaps.\n')
    harness.write_new_text(output/'REPORT.md', report)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-run', required=True)
    parser.add_argument('--candidate-artifacts', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--root', default=str(harness.ROOT))
    args = parser.parse_args(argv)
    summary = rescore(args.input_run, args.candidate_artifacts, args.output, root=args.root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
