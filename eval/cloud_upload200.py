"""Frozen NEW-company acceptance: original-PDF oracle, Java/SSE, immutable first results.

No provider SDK is imported. ``prepare`` is entirely offline; only explicit ``run``
uses the user's existing Java service and its private server-side model settings.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.parse import unquote, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = ROOT/'eval/cloud_upload200_cases.json'
SUITE_SHA256 = '8fa4ed06279a9369f38b837a6a3adff3a733be25fc9a7356f4c82b9bffcc3e7b'
QUESTIONS_SHA256 = 'a858576d2b62cfb90465c66b5701117986d02189bdf4460c2acdbf6f35382851'
ORACLE_CALIBRATION_VERSION = 'physical-label-and-deliverables-v1'
COHORT = {
    '000333': ('美的集团', 'development'), '002594': ('比亚迪', 'development'),
    '600519': ('贵州茅台', 'development'), '600900': ('长江电力', 'development'),
    '601668': ('中国建筑', 'development'), '002475': ('立讯精密', 'development'),
    '300750': ('宁德时代', 'holdout'), '600887': ('伊利股份', 'holdout'),
    '601088': ('中国神华', 'holdout'), '600031': ('三一重工', 'holdout'),
}
FACTORS = {'元': Decimal(1), '千元': Decimal(1000), '万元': Decimal(10000),
           '百万元': Decimal(1000000), '亿元': Decimal(100000000), '%': Decimal(1),
           '元/股': Decimal(1), '百分点': Decimal(1)}
FINANCIAL = {'lookup', 'compare', 'rank', 'chart', 'quote', 'cause', 'sign'}
SOURCE_FIELDS = ('source_path', 'source_sha256', 'document_version', 'page', 'table',
                 'row', 'column', 'raw_value', 'raw_unit')


class FreezeMismatch(RuntimeError):
    """The questions, source, release, model, or first-result history changed."""


class OracleMissing(RuntimeError):
    def __init__(self, gaps):
        self.gaps = gaps
        super().__init__(f'Original-based oracle is incomplete ({len(gaps)} gaps); cloud execution blocked')


class OriginalAuditIncomplete(ValueError):
    """A candidate exists, but its original-source audit has not been completed."""


class PendingSubmission(RuntimeError):
    """Remote admission is uncertain. Resume only the persisted request identity."""


class RunStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Run stopped: '+reason)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def value_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            sha.update(block)
    return sha.hexdigest()


def write_new(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
        stream.flush(); os.fsync(stream.fileno())


def write_new_text(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(value); stream.flush(); os.fsync(stream.fileno())


def replace_derived(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    write_new_text(temporary, value)
    os.replace(temporary, path)


def normalize(value):
    return re.sub(r'[\s,，]', '', unicodedata.normalize('NFKC', str(value))).replace('−', '-').replace('－', '-')


def _label_key(value):
    """Normalize annotation typography only; never use this for numeric literals."""
    text = unicodedata.normalize('NFKC', str(value))
    return re.sub(r'\s+', '', text).translate(str.maketrans({char: '-' for char in '‐‑‒–—―−﹘﹣－'}))


def parse_decimal(value):
    text = normalize(value).replace('%', '').replace('％', '')
    if text.startswith(('(', '（')) and text.endswith((')', '）')):
        text = '-'+text[1:-1]
    result = Decimal(text)
    if not result.is_finite():
        raise ValueError('Non-finite numeric value')
    return result


def format_number(value, unit, decimals=2):
    with localcontext() as context:
        context.prec = 60
        result = parse_decimal(value)/FACTORS[unit]
        return format(result.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP), f'.{decimals}f')


def local_path(value, root=ROOT):
    """Accept local paths and file URIs, never fetch an external source."""
    text = str(value)
    if text.startswith('file:'):
        parsed = urlsplit(text)
        if parsed.netloc and parsed.netloc not in {'localhost'}:
            raise ValueError('Remote file URI is not an original local artifact')
        text = unquote(parsed.path)
        if re.match(r'^/[A-Za-z]:/', text):
            text = text[1:]
    elif '://' in text:
        raise ValueError('Only local candidate artifacts are accepted')
    path = Path(text)
    return (path if path.is_absolute() else Path(root)/path).resolve()


def load_cases(path=CASE_FILE):
    if digest(path) != SUITE_SHA256:
        raise FreezeMismatch('Frozen upload200 suite bytes changed')
    suite = read(path); cases = suite['cases']
    fields = ('id', 'partition', 'company_code', 'group', 'turn', 'question')
    if value_digest([{key: case[key] for key in fields} for case in cases]) != QUESTIONS_SHA256:
        raise FreezeMismatch('Frozen question hash changed')
    validate_cases(cases)
    return cases


def validate_cases(cases):
    if len(cases) != 200 or len({c['id'] for c in cases}) != 200:
        raise FreezeMismatch('Expected exactly 200 distinct frozen turns')
    if Counter(c['partition'] for c in cases) != Counter(development=120, holdout=80):
        raise FreezeMismatch('The company split must be development120/holdout80')
    if Counter(c['company_code'] for c in cases) != Counter({code: 20 for code in COHORT}):
        raise FreezeMismatch('Each new company must have 20 turns')
    groups = {c['group'] for c in cases if c['group']}
    if len(groups) != 20 or sum(c['group'] is None for c in cases) != 120:
        raise FreezeMismatch('Expected 120 singles and 20 four-turn groups')
    for code in COHORT:
        if sum(c['company_code'] == code and not c['group'] for c in cases) != 12:
            raise FreezeMismatch('Each company needs 12 single-turn questions')
    for group in groups:
        rows = [c for c in cases if c['group'] == group]
        if [c['turn'] for c in rows] != [1, 2, 3, 4] or len({c['partition'] for c in rows}) != 1 or len({c['company_code'] for c in rows}) != 1:
            raise FreezeMismatch('Dialogue groups must remain intact on one side')
    for case in cases:
        if case['partition'] != COHORT[case['company_code']][1]:
            raise FreezeMismatch('A company crossed the held-out split')
        for spec in case['expect']['goal_expectations']:
            if any(code not in COHORT or COHORT[code][1] != case['partition'] for code in spec.get('codes', [])):
                raise FreezeMismatch('A goal crossed the held-out company split')


def identity(fact):
    return (fact.get('stock_code'), fact.get('year'), fact.get('period'), fact.get('metric'), fact.get('scope'))


def spec_identities(spec):
    pairs = spec.get('pairs') or [(year, period) for year in spec.get('years', []) for period in spec.get('periods', ['FY'])]
    return {(code, year, period, metric, spec.get('scope', 'consolidated'))
            for code in spec.get('codes', []) for year, period in pairs for metric in spec.get('metrics', [])}


def selected_facts(spec, reference, *, rank_inputs=False):
    wanted = spec_identities(spec)
    rows = [fact for fact in reference['facts'].values() if identity(fact) in wanted]
    if spec.get('kind') == 'rank' and not rank_inputs:
        rows.sort(key=lambda fact: (-parse_decimal(fact['value']) if spec.get('order', 'desc') == 'desc' else parse_decimal(fact['value']), fact['stock_code']))
        rows = rows[:spec.get('limit', len(rows))]
    return rows


def load_release(candidate_artifacts, *, root=ROOT, require_cohort=True):
    folder = local_path(candidate_artifacts, root)
    narratives = tuple(name for name in ('narratives.jsonl', 'narratives.json') if (folder/name).is_file())
    required = ('reports.json', 'facts.json', 'manifest.json')+(narratives or ('narratives.jsonl',))
    if any(not (folder/name).is_file() for name in required):
        raise OracleMissing([dict(reason='release_artifact_missing', file=name) for name in required if not (folder/name).is_file()])
    required += tuple(name for name in ('index.json', 'index_coverage.json', 'publication.json') if (folder/name).is_file())
    reports, facts, manifest = read(folder/'reports.json'), read(folder/'facts.json'), read(folder/'manifest.json')
    if not isinstance(reports, list) or not isinstance(facts, list) or not facts:
        raise OracleMissing([dict(reason='invalid_or_empty_release')])
    versions = {fact.get('data_version') for fact in facts}
    if len(versions) != 1 or None in versions:
        raise FreezeMismatch('Candidate facts must share one release version')
    version = next(iter(versions))
    if manifest.get('data_version', manifest.get('version', version)) != version:
        raise FreezeMismatch('Manifest and facts disagree about the release version')
    if len({fact.get('id') for fact in facts}) != len(facts) or None in {fact.get('id') for fact in facts}:
        raise FreezeMismatch('Candidate fact IDs must be unique')
    identities = [identity(fact) for fact in facts]
    if len(identities) != len(set(identities)):
        raise OracleMissing([dict(reason='conflicting_duplicate_fact_identity')])
    inventory = {(r.get('stock_code'), r.get('year'), r.get('period')) for r in reports}
    expected = {(code, year, period) for code in COHORT for year in (2023, 2024, 2025) for period in ('FY', 'HY', 'Q1', 'Q3')}
    if not inventory.issubset(expected) or len(inventory)!=len(reports):
        raise FreezeMismatch('Published report inventory contains unexpected or duplicate identities')
    if require_cohort and (inventory != expected or len(reports) != 120):
        raise OracleMissing([dict(reason='inventory_incomplete', missing=sorted(expected-inventory), extra=sorted(inventory-expected), reports=len(reports))])
    documents = {}
    from pypdf import PdfReader
    for report in reports:
        path = local_path(report['source_path'], root)
        if not path.is_relative_to(Path(root).resolve()) or path.suffix.lower() != '.pdf' or not path.is_file():
            raise OracleMissing([dict(reason='original_pdf_missing_or_outside_candidate', source_path=report.get('source_path'))])
        sha = digest(path)
        if sha != report.get('document_version') or report.get('source_sha256', sha) != sha:
            raise FreezeMismatch('Original PDF digest differs from registered report')
        page_count = len(PdfReader(path).pages)
        if int(report.get('page_count', page_count)) != page_count:
            raise FreezeMismatch('Original PDF page count differs from registered report')
        documents[str(path.relative_to(Path(root).resolve()))] = sha
    if require_cohort and len(set(documents.values())) != 120:
        raise OracleMissing([dict(reason='original_reports_are_not_120_distinct_pdfs')])
    return dict(version=version, facts={fact['id']: fact for fact in facts}, reports=reports,
                manifest=manifest, artifacts={name: digest(folder/name) for name in required},
                documents=documents, folder=str(folder),inventory_gaps=[list(row) for row in sorted(expected-inventory)])


def _pdf_page(path, page, readers, pages):
    from pypdf import PdfReader
    key = str(path)
    if key not in readers:
        readers[key] = PdfReader(path)
    if not isinstance(page, int) or not 1 <= page <= len(readers[key].pages):
        raise ValueError('Source page is outside original PDF')
    if (key, page) not in pages:
        pages[key, page] = readers[key].pages[page-1].extract_text() or ''
    return pages[key, page]


def _region_text(path, page, bbox):
    from src.utils.original_regions import original_region_text
    stat = Path(path).stat()
    return original_region_text(str(path), stat.st_size, stat.st_mtime_ns, page, tuple(bbox))


def _check_original_regions(source, path, page_text):
    """Re-read the declared rectangles, including scope and period column proofs."""
    cell = source.get('original_cell') or {}
    scope = source.get('scope_proof') or {}
    regions = [(name, region) for name, region in cell.items() if isinstance(region, dict) and 'bbox' in region]
    regions += [(name, region) for name, region in scope.items() if isinstance(region, dict) and 'bbox' in region]
    for name, region in regions:
        text = _region_text(path, region.get('page', source['page']), region['bbox'])
        literal = region.get('text')
        if literal and normalize(literal) not in normalize(text):
            raise ValueError('Original source proof rectangle no longer contains its literal: '+name)
    value_region = cell.get('value') or scope.get('value_region')
    return _region_text(path, value_region.get('page', source['page']), value_region['bbox']) if value_region else page_text


def _original_unit_present(unit, original):
    text = normalize(original)
    patterns = {'元': r'(?<![十百千万亿])元(?![/／股])|(?<![A-Za-z])(?:CNY|RMB)(?![A-Za-z])',
        '千元': r'(?<![十百千万亿])千元', '万元': r'(?<![十百千万亿])万元',
        '百万元': r'(?<![十百千万亿])百万元', '亿元': r'(?<![十百千万亿])亿元',
        '%': r'%', '元/股': r'元[/／]股'}
    return unit in patterns and bool(re.search(patterns[unit], text))


def _oracle_reference_digest(reference):
    return value_digest(dict(data_version=reference['version'], facts=reference['facts'],
                             reports=reference['reports'], artifacts=reference.get('artifacts', {})))


def build_oracle(cases, reference, *, root=ROOT,record_data_gaps=False):
    """Independent Decimal values re-read from originals, never from an answer.

    Missing supported metrics are blocking coverage gaps. Image-only originals
    whose actual PDF regions cannot be re-read also block; cached OCR assertions
    are never silently promoted into an independently checked oracle.
    """
    gaps, needed, case_ids = [], set(), {}
    index = {identity(fact): fact for fact in reference['facts'].values()}
    reports = {(report['stock_code'], report['year'], report['period']): report for report in reference['reports']}
    for case in cases:
        if case['expect']['kind'] in {'clarify', 'unsupported', 'greeting', 'concept'}:
            continue
        for spec in case['expect']['goal_expectations']:
            if spec['kind'] not in FINANCIAL:
                continue
            for key in sorted(spec_identities(spec)):
                fact = index.get(key)
                if fact is None:
                    gaps.append(dict(case_id=case['id'], reason='metric_not_extracted', identity=list(key)))
                else:
                    needed.add(fact['id']); case_ids.setdefault(fact['id'], []).append(case['id'])
    readers, pages, cards, documents = {}, {}, {}, {}
    quote_ids = {fact['id'] for case in cases for spec in case['expect']['goal_expectations']
                 if spec['kind'] == 'quote' for fact in selected_facts(spec, reference)}
    visiting = set()
    def verify(fact_id):
        if fact_id in cards:
            return cards[fact_id]
        if fact_id in visiting:
            raise ValueError('Cyclic derived fact inputs')
        visiting.add(fact_id)
        fact = reference['facts'][fact_id]; source = fact.get('source')
        if fact.get('status') == 'unverified' or (source or {}).get('verification') == 'unverified':
            raise OriginalAuditIncomplete('OCR candidate exists; original cell/scope audit remains incomplete')
        from src.agent.v3.catalog import METRICS
        metric = METRICS.get(fact['metric'])
        if not metric or fact['unit'] != metric.unit or fact['scope'] not in metric.scopes:
            raise ValueError('Fact metric, unit or scope differs from the registered financial definition')
        report = reports.get((fact['stock_code'], fact['year'], fact['period']))
        if not report or fact['company'] != report['company'] or fact['data_version'] != reference['version']:
            raise ValueError('Fact company or period differs from the registered original report')
        if source:
            if fact.get('inputs') or fact.get('formula') or fact.get('status') not in {None, 'verified'}:
                raise ValueError('A direct original fact carries invalid status or derived lineage')
            path = local_path(source['source_path'], root)
            if not path.is_relative_to(Path(root).resolve()) or path.suffix.lower() != '.pdf':
                raise ValueError('Fact original is outside candidate root')
            if (path != local_path(report['source_path'], root) or
                source['document_version'] != report['document_version']):
                raise ValueError('Fact source differs from its registered company/report period')
            sha = digest(path)
            if sha != source['source_sha256'] or sha != source['document_version']:
                raise ValueError('Fact original digest differs')
            original = _pdf_page(path, source['page'], readers, pages)
            region = _check_original_regions(source, path, original)
            checked_cell = None
            if source.get('original_cell') or source.get('scope_proof'):
                from src.agent.v3.contracts import Fact
                from src.utils.original_regions import checked_regions, checked_cell_binding
                from src.utils.statement_scope import checked_statement_scope
                observed = Fact.model_validate(fact)
                if not checked_statement_scope(observed, path):
                    raise ValueError('Actual original statement scope proof failed revalidation')
                checked_cell = observed.source.original_cell
                if checked_cell and not (checked_regions(path, checked_cell)
                        and checked_cell_binding(observed, checked_cell, path)):
                    raise ValueError('Actual original row, period column or unit proof failed revalidation')
            raw = source['raw_value']
            from src.utils.original_cells import original_literal, normalized_positions
            # A physical cell may wrap a decimal across lines. Join its characters
            # only after independently rechecking that exact cell and its binding.
            bounded_lexeme = bool(checked_cell and region == checked_cell.value.text and
                normalized_positions(region, ignore_commas=True)[0] == normalized_positions(raw, ignore_commas=True)[0])
            if not normalize(raw) or (not bounded_lexeme and
                    original_literal(region, raw.replace('%', '').replace('％', ''), numeric=True) is None):
                raise ValueError('Raw value is absent from actual PDF region')
            if 'raw_precision' in source and source['raw_precision'] != max(0, -parse_decimal(raw).as_tuple().exponent):
                raise ValueError('Claimed raw precision differs from the original numeric literal')
            row = normalize(source.get('row', '')).removeprefix('其中:').removeprefix('其中：')
            if checked_cell:
                from src.utils.financial_row_identity import checked_row_identity
                # Scope, value, date, unit and every physical rectangle have already
                # been re-read above. OCR punctuation is compared to that exact row,
                # and both labels must independently bind the canonical metric.
                physical = observed.model_copy(deep=True)
                physical.source.row = checked_cell.row.text
                if (_label_key(source.get('row', '')) != _label_key(checked_cell.row.text)
                        or not checked_row_identity(observed) or not checked_row_identity(physical)):
                    raise ValueError('Requested row label differs from the checked canonical physical row')
            elif not row or row not in normalize(original):
                raise ValueError('Requested row label is absent from actual PDF page')
            # The number, native unit and table scope remain a single source identity.
            unit = source['raw_unit']
            amount_units = {'元', '千元', '万元', '百万元', '亿元'}
            if unit not in FACTORS or (fact['unit'] == '元' and unit not in amount_units) or (fact['unit'] != '元' and unit != fact['unit']):
                raise ValueError('Unsupported source unit')
            unit_context = (_region_text(path, checked_cell.context.page, checked_cell.context.bbox)
                            if checked_cell else '')
            if not (_original_unit_present(unit, original) or
                    checked_cell and unit_context == checked_cell.context.text and _original_unit_present(unit, unit_context)):
                raise ValueError('Source unit is not present on actual PDF page')
            with localcontext() as context:
                context.prec = 60
                exact = parse_decimal(raw)*FACTORS[unit]
            if exact != parse_decimal(fact['value']):
                raise ValueError('Original Decimal normalization differs from extracted fact')
            matching_rows = [line for line in original.splitlines() if row in normalize(line) or normalize(raw) in normalize(line)]
            card = dict(identity={key: fact[key] for key in ('stock_code', 'company', 'year', 'period', 'metric', 'scope', 'unit')},
                        value_exact=format(exact, 'f'), source=copy.deepcopy(source),
                        original_row_text='\n'.join(matching_rows) or region,
                        original_region_text=region, original_page_text=original,
                        verification='actual original PDF text/rectangles plus independent Decimal unit conversion')
            if checked_cell:
                card['original_label_text'] = checked_cell.row.text
            if fact_id in quote_ids and (source.get('original_cell') or source.get('scope_proof')):
                from src.utils.original_rows import original_table_row
                card['original_table_row'] = original_table_row(path, observed.source)
            documents[source['source_path']] = sha
        else:
            inputs = fact.get('inputs', [])
            formulas = {'gross_profit': ('operating_revenue', 'operating_cost'),
                        'gross_margin': ('gross_profit', 'operating_revenue'),
                        'net_margin': ('net_profit', 'operating_revenue'),
                        'debt_ratio': ('total_liabilities', 'total_assets')}
            if len(inputs) != 2 or len(set(inputs)) != 2:
                raise ValueError('Derived fact requires two original-verified inputs')
            input_facts = [reference['facts'][key] for key in inputs]
            expected_inputs = formulas.get(fact['metric'])
            if not expected_inputs or tuple(row['metric'] for row in input_facts) != expected_inputs:
                raise ValueError('Derived input metrics differ from the independent formula')
            conditions = ('stock_code', 'company', 'year', 'period', 'scope', 'data_version')
            if any(any(row.get(key) != fact.get(key) for key in conditions) for row in input_facts):
                raise ValueError('Derived inputs cross company, report period, scope or data version')
            formula = ' - '.join(expected_inputs) if fact['metric'] == 'gross_profit' else ' / '.join(expected_inputs)+' * 100'
            if fact.get('formula') != formula:
                raise ValueError('Derived formula differs from the independent financial definition')
            bases = [verify(key) for key in inputs]
            with localcontext() as context:
                context.prec = 50
                left, right = (parse_decimal(card['value_exact']) for card in bases)
                if fact['metric'] == 'gross_profit':
                    exact = left-right
                elif fact['metric'] in {'gross_margin', 'net_margin', 'debt_ratio'}:
                    exact = left/right*100
                else:
                    raise ValueError('Unrecognized independent derived metric formula')
            if exact != parse_decimal(fact['value']):
                raise ValueError('Independent derived metric differs')
            card = dict(identity={key: fact[key] for key in ('stock_code', 'company', 'year', 'period', 'metric', 'scope', 'unit')},
                        value_exact=format(exact, 'f'), inputs=fact['inputs'], formula=fact.get('formula'),
                        verification='independent Decimal formula using original-verified bases')
        visiting.remove(fact_id); cards[fact_id] = card
        return card
    for fact_id in sorted(needed):
        try:
            verify(fact_id)
        except OriginalAuditIncomplete as exc:
            visiting.clear()
            gaps.append(dict(reason='original_audit_incomplete', fact_id=fact_id,
                             case_ids=sorted(set(case_ids.get(fact_id, []))), detail=str(exc)))
        except (ValueError, KeyError, OSError, InvalidOperation, ZeroDivisionError) as exc:
            visiting.clear()
            gaps.append(dict(reason='original_revalidation_failed', fact_id=fact_id,
                             case_ids=sorted(set(case_ids.get(fact_id, []))), detail=str(exc)))
    if gaps and not record_data_gaps:
        raise OracleMissing(gaps)
    calculations = {}
    for case in cases:
        calculations[case['id']] = independent_calculations(case, reference, dict(cards=cards))
    return dict(schema=2, calibration_version=ORACLE_CALIBRATION_VERSION,
                data_version=reference['version'], questions_sha256=QUESTIONS_SHA256,
                reference_sha256=_oracle_reference_digest(reference),
                cards=cards, documents=documents, calculations=calculations,data_gaps=gaps,
                review_policy='Cause/concept prose is pending independent assistant review; never grade with tested model')


def independent_calculations(case, reference, oracle):
    rows = []
    for goal_index, spec in enumerate(case['expect']['goal_expectations']):
        method = spec.get('calculation')
        if not method or method == 'none':
            continue
        facts = selected_facts(spec, reference, rank_inputs=True)
        buckets = {}
        for fact in facts:
            key = (fact['stock_code'], fact['metric'], fact['scope'], fact['period'])
            buckets.setdefault(key, []).append(fact)
        for values in buckets.values():
            values.sort(key=lambda fact: fact['year'])
            if len(values) != 2:
                continue
            base, current = values
            if any(fact['id'] not in oracle['cards'] for fact in values):
                continue
            with localcontext() as context:
                context.prec = 60
                before, after = (parse_decimal(oracle['cards'][fact['id']]['value_exact']) for fact in values)
                if method == 'difference':
                    value, unit = after-before, current['unit']
                elif method == 'percentage_points':
                    value, unit = after-before, '百分点'
                elif method in {'yoy', 'relative_percent'}:
                    value, unit = ((after-before)/abs(before)*100 if before else None), '%'
                else:
                    raise ValueError('Unknown independent calculation')
            rows.append(dict(goal_index=goal_index, calculation=method, inputs=[base['id'], current['id']],
                             value_exact=None if value is None else format(value, 'f'), unit=unit,
                             status='undefined_zero_base' if value is None else 'defined'))
    return rows


def _effective_goal(observed, request):
    resolved = (request.get('resolved_goal_conditions') or {}).get(observed.get('id'))
    if isinstance(resolved, dict):
        return copy.deepcopy(resolved)
    conditions = copy.deepcopy(observed.get('context_conditions') or request.get('conditions') or {})
    edits = [*(request.get('modifications', []) if observed.get('context_conditions') else []), *observed.get('condition_edits', [])]
    for edit in edits:
        parts = edit['field'].split('.'); target = conditions
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        leaf = parts[-1]; operation = edit['operation']; value = edit.get('value'); old = target.get(leaf)
        if operation in {'replace', 'keep'}:
            target[leaf] = value
        elif operation == 'add':
            target[leaf] = list(dict.fromkeys([*(old or []), *(value or [])]))
        elif operation == 'remove':
            target[leaf] = [item for item in old or [] if item not in value]
        elif operation == 'clear':
            target[leaf] = [] if isinstance(old, list) else None
    for key, value in (observed.get('selection') or {}).items():
        if value is not None:
            conditions[key] = {**(conditions.get(key) or {}), **value} if key in {'time', 'presentation', 'restrictions'} else value
    return conditions


def condition_errors(spec, conditions):
    errors = []
    if 'all_companies' in spec and bool(conditions.get('all_companies'))!=spec['all_companies']:
        errors.append('all_companies')
    for field in ('codes', 'metrics'):
        if field in spec and set(conditions.get(field) or []) != set(spec[field]):
            errors.append(field)
    if spec.get('codes') and conditions.get('scope', 'consolidated') != spec.get('scope', 'consolidated'):
        errors.append('scope')
    observed_time = conditions.get('time') or {}
    pairs = observed_time.get('pairs') or []
    for name, actual in [('years', {pair[0] for pair in pairs} or set(observed_time.get('years') or [])),
                         ('periods', {pair[1] for pair in pairs} or set(observed_time.get('periods') or ['FY']))]:
        timeless_definition = spec.get('kind') == 'concept' and not spec.get('years') and not spec.get('pairs')
        if spec.get(name) and not timeless_definition and actual != set(spec[name]):
            errors.append(name)
    presentation = conditions.get('presentation') or {}
    for name in ('unit', 'decimals', 'format', 'order', 'limit', 'include_inputs'):
        if name in spec and presentation.get(name) != spec[name]:
            errors.append(name)
    if spec.get('chart') and presentation.get('chart_type', 'line') != spec['chart']:
        errors.append('chart_type')
    if spec.get('calculation') and conditions.get('calculation', 'none') != spec['calculation']:
        errors.append('calculation')
    if spec.get('axis') and conditions.get('comparison_axis', 'none') != spec['axis']:
        errors.append('comparison_axis')
    for name in ('no_query','no_chart','no_repeat'):
        if name in spec and bool((conditions.get('restrictions') or {}).get(name)) != spec[name]:errors.append(name)
    for name in ('mode','span','single_quarter'):
        if name in spec and observed_time.get(name,False if name=='single_quarter' else None) != spec[name]:errors.append(name)
    return errors


def _metric_unit(conditions, metric):
    presentation = conditions.get('presentation') or {}
    explicit = (presentation.get('metric_units') or {}).get(metric)
    if explicit is not None:
        return explicit
    from src.agent.v3.catalog import METRICS
    definition = METRICS.get(metric)
    native = definition.unit if definition else None
    global_unit = presentation.get('unit')
    amount_units = {'元', '千元', '万元', '百万元', '亿元'}
    if global_unit == native or native == '元' and global_unit in amount_units:
        return global_unit
    return native


def _matching_errors(spec, conditions, expectations):
    spec = copy.deepcopy(spec); conditions = copy.deepcopy(conditions)
    if spec['kind'] == 'quote':
        # A quote delivers the native source. A separate lookup goal represents
        # any requested converted amount and retains its presentation checks.
        spec.pop('unit', None); spec.pop('decimals', None)
    if spec['kind'] == 'lookup':
        spec.setdefault('calculation', 'none')
        peers = [peer for peer in expectations if peer['kind'] == 'lookup'
                 and not condition_errors({key: value for key, value in peer.items()
                                           if key not in {'metrics', 'unit'}}, conditions)]
        allowed_metrics = {metric for peer in peers for metric in peer.get('metrics', [])}
        actual_metrics = set(conditions.get('metrics') or [])
        if set(spec.get('metrics', [])) <= actual_metrics <= allowed_metrics:
            conditions['metrics'] = spec.get('metrics', [])
        if 'unit' in spec and spec.get('metrics'):
            units = {_metric_unit(conditions, metric) for metric in spec['metrics']}
            conditions.setdefault('presentation', {})['unit'] = next(iter(units)) if len(units) == 1 else None
    return condition_errors(spec, conditions)


def _input_lookup(goal, request, expectations):
    """A lookup may deliver compare inputs, with no additional years or operation."""
    if goal.get('kind') != 'lookup':
        return False
    conditions = _effective_goal(goal, request); time_spec = conditions.get('time') or {}
    if conditions.get('calculation', 'none') != 'none' or time_spec.get('single_quarter'):
        return False
    observed = dict(codes=conditions.get('codes', []), metrics=conditions.get('metrics', []),
                    scope=conditions.get('scope', 'consolidated'), years=time_spec.get('years', []),
                    periods=time_spec.get('periods', ['FY']), pairs=time_spec.get('pairs'))
    identities = spec_identities(observed)
    for spec in expectations:
        if spec['kind'] != 'compare' or not identities or not identities <= spec_identities(spec):
            continue
        presentation = {key: spec[key] for key in ('decimals', 'format') if key in spec}
        unit_matches = 'unit' not in spec or all(_metric_unit(conditions, metric) == spec['unit'] for metric in observed['metrics'])
        if unit_matches and not condition_errors(presentation, conditions):
            return True
    return False


def match_goals(expectations, request):
    """Match requested deliverables, allowing equivalent grouping of lookup metrics."""
    actual = request.get('goals') or []
    def compatible(spec,goal):
        if goal.get('kind')==spec['kind']:return True
        # 数值目标由条件中的运算、轴、排序和数量定义；概念、引用和图表不能借标签等价混过检查。
        if spec['kind']=='compare' and goal.get('kind')=='lookup':
            return spec.get('calculation','none')!='none'
        if spec['kind']=='rank' and goal.get('kind') in {'lookup','compare'}:
            return bool(spec.get('order') and spec.get('limit'))
        return False
    candidates = [[(index, _matching_errors(spec, _effective_goal(goal, request), expectations))
                   for index, goal in enumerate(actual) if compatible(spec,goal)] for spec in expectations]
    best = None
    def choose(position, used, selected, errors):
        nonlocal best
        if position == len(expectations):
            extras = {index for index, goal in enumerate(actual) if index not in used
                      and not _input_lookup(goal, request, expectations)}
            total = len(errors)+len(extras)
            if best is None or total < best[0]:
                best = total, selected[:], errors[:], used.copy()
            return
        options = [(index, issues) for index, issues in candidates[position] if index not in used
                   or actual[index].get('kind') == 'lookup' and not issues]
        if not options:
            choose(position+1, used, selected+[None], errors+[f'goal_{position+1}_missing_{expectations[position]["kind"]}'])
        for index, issues in options:
            choose(position+1, used | {index}, selected+[actual[index]], errors+[f'goal_{position+1}_{issue}' for issue in issues])
    choose(0, set(), [], [])
    _, selected, errors, used = best
    if any(index not in used and not _input_lookup(goal, request, expectations) for index, goal in enumerate(actual)):
        errors.append('extra_unrequested_goals')
    return selected, errors


def _body(result):
    answer = result.get('answer') or {}
    return answer.get('content', '') if isinstance(answer, dict) else str(answer)


def _visible_number(body, number, unit, decimals):
    """Require the displayed rounded number and unit in the same clause/table row."""
    target = format_number(number, unit, decimals)
    for line in str(body).splitlines():
        for match in re.finditer(r'(?<![\d.])[-−]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?(?![\d.])', line):
            token = match.group()
            try:
                if parse_decimal(token) != parse_decimal(target):
                    continue
            except (ValueError, InvalidOperation):
                continue
            if decimals and ('.' not in token or len(token.rsplit('.', 1)[1]) != decimals):
                continue
            if not decimals and '.' in token:
                continue
            clause = line[max(0, match.start()-100):match.end()+25]
            if _visible_unit(unit, clause):
                return True
    # Tables may declare the native unit in their header or a preceding unit line.
    header = '\n'.join(str(body).splitlines()[:5])
    if _visible_unit(unit, header):
        for line in str(body).splitlines():
            if not line.lstrip().startswith('|'):
                continue
            for token in re.findall(r'[-−]?\d[\d,，]*(?:\.\d+)?', line):
                if parse_decimal(token) == parse_decimal(target) and (len(token.rsplit('.', 1)[1]) if '.' in token else 0) == decimals:
                    return True
    return False


def _visible_unit(unit, text):
    if unit == '元':
        return bool(re.search(r'(?<![千万元亿百])元(?![/／]股)', text))
    if unit == '万元':
        return bool(re.search(r'(?<!百)万元', text))
    return unit in text or unit == '%' and '％' in text


def score_case(case, run, reference, oracle, *, model_version):
    """Score every goal and published fact independently of server verification flags."""
    expected = case['expect']; task = run.get('task') or {}; result = task.get('result') or {}
    request = result.get('request_contract') or {}; body = _body(result)
    facts = result.get('facts') or []; charts = result.get('chart_data_list') or []
    errors, review = [], list(expected.get('manual_review', []))
    def require(condition, message):
        if not condition:
            errors.append(message)
    require(task.get('status') == 'completed' and task.get('saved') is True, 'task_not_completed_and_saved')
    require(bool(str(body).strip() or charts), 'empty_output')
    require(bool(request), 'missing_request_contract')
    timings = (result.get('diagnostics') or {}).get('timings') or []
    require(bool(timings) and all(row.get('model') == model_version for row in timings), 'model_identity_not_confirmed')
    traces = result.get('query_trace') or []
    require(not any(re.search(r'\b(?:DELETE|UPDATE|INSERT|DROP|ALTER|CREATE|GRANT|TRUNCATE)\b', str(trace.get('sql', '')), re.I) for trace in traces), 'write_sql_executed')
    financial_queries = [trace for trace in traces if trace.get('purpose') == 'financial_facts']
    if expected.get('no_query'):
        require(not financial_queries and result.get('sql') in {None, '', '-'}, 'forbidden_numeric_query')
    if expected.get('query_required'):
        require(bool(financial_queries), 'new_financial_query_missing')
    if expected.get('no_chart'):
        require(not charts, 'forbidden_chart')
    mode = expected['kind']; outcome = (result.get('outcome') or {}).get('status')
    if mode in {'clarify', 'unsupported', 'greeting', 'concept'}:
        require(not facts and not charts and not result.get('derived_facts'), 'financial_output_without_supported_request')
        if mode == 'clarify':
            require(outcome == 'needs_clarification' or result.get('needs_clarification') is True, 'required_clarification_missing')
            conditions = [_effective_goal(goal, request) for goal in request.get('goals') or []]
            for name in ('codes', 'metrics', 'years'):
                key = 'known_'+name
                if key in expected:
                    actual = set().union(*(set((condition.get('time') or {}).get('years') or []) if name == 'years' else set(condition.get(name) or []) for condition in conditions)) if conditions else set()
                    require(actual == set(expected[key]), 'clarification_'+name+'_lost')
        elif mode == 'unsupported':
            require(outcome in {'unsupported', 'needs_clarification'} or bool(request.get('unsupported')), 'unsupported_boundary_missing')
        elif mode == 'greeting':
            require(not financial_queries, 'greeting_queried_financial_numbers')
        else:
            require(not financial_queries, 'concept_queried_financial_numbers'); review.append('concept_prose')
        matched = []
    else:
        require(outcome == 'answered', 'supported_request_not_completed')
        matched, match_errors = match_goals(expected['goal_expectations'], request)
        errors.extend(match_errors)
        allowed = {}
        needed_oracle = set()
        for spec in expected['goal_expectations']:
            if spec['kind'] not in FINANCIAL:
                if spec['kind'] == 'concept':
                    review.append('concept_prose')
                continue
            rows = selected_facts(spec, reference)
            all_rows = selected_facts(spec, reference, rank_inputs=True)
            require({identity(fact) for fact in all_rows} == spec_identities(spec), 'oracle_metric_not_extracted')
            allowed.update({fact['id']: fact for fact in rows}); needed_oracle.update(fact['id'] for fact in all_rows)
        missing = sorted(needed_oracle-set(oracle.get('cards', {})))
        if missing or 'oracle_metric_not_extracted' in errors:
            return dict(status='oracle_missing', passed=False, automated_pass=False, review_status='blocked',
                        errors=sorted(set(errors+['oracle_missing'])), oracle_missing=missing, review_required=review)
        seen = [fact.get('id') for fact in facts]
        require(len(seen) == len(set(seen)), 'duplicate_published_fact')
        require(set(seen) == set(allowed), 'missing_or_extra_requested_facts')
        require(not set(seen)-set(allowed), 'out_of_request_fact_published')
        if allowed:
            require(result.get('data_version') == reference['version'], 'data_version_changed')
        for fact in facts:
            truth = reference['facts'].get(fact.get('id')); card = oracle['cards'].get(fact.get('id'))
            require(bool(truth and card), 'unverified_fact_published')
            if not truth or not card:
                continue
            require(all(fact.get(key) == truth.get(key) for key in ('stock_code', 'year', 'period', 'metric', 'scope', 'unit', 'data_version')), 'fact_identity_changed')
            company_name = lambda value: re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold() if isinstance(value, str) else ''
            require(bool(company_name(truth.get('company'))) and
                    company_name(fact.get('company')) == company_name(truth['company']), 'fact_identity_changed')
            try:
                require(parse_decimal(fact['value_exact']) == parse_decimal(card['value_exact']), 'wrong_numeric_fact')
            except (KeyError, InvalidOperation, ValueError):
                require(False, 'wrong_numeric_fact')
            if truth.get('source'):
                require(all((fact.get('source') or {}).get(key) == truth['source'].get(key) for key in SOURCE_FIELDS), 'wrong_source')
        statuses = {row.get('id'): row for row in result.get('task_results') or []}
        matched_ids = {goal['id'] for goal in matched if goal}
        for goal in request.get('goals') or []:
            if goal['id'] not in matched_ids and _input_lookup(goal, request, expected['goal_expectations']):
                require((statuses.get(goal['id']) or {}).get('status') == 'completed', 'input_lookup_not_completed')
        for index, (spec, observed) in enumerate(zip(expected['goal_expectations'], matched)):
            if not observed:
                continue
            require((statuses.get(observed['id']) or {}).get('status') == 'completed', f'goal_{index+1}_not_completed')
            rows = selected_facts(spec, reference) if spec['kind'] in FINANCIAL else []
            if spec['kind'] not in {'chart', 'cause', 'quote'}:
                for fact in rows:
                    require(_visible_number(body, oracle['cards'][fact['id']]['value_exact'], spec.get('unit', fact['unit']), spec.get('decimals', 2)), 'wrong_or_missing_visible_value_unit_precision')
                    require(fact['company'] in body or fact['stock_code'] in body, 'visible_company_missing')
                    require(str(fact['year']) in body, 'visible_year_missing')
            if spec.get('format') == 'table':
                require(bool(re.search(r'^\s*\|.*\|\s*$', body, re.M)), f'goal_{index+1}_table_missing')
            if spec['kind'] == 'rank':
                positions = []
                for fact in rows:
                    matches = [body.find(fact['company']), body.find(fact['stock_code'])]
                    positions.append(min((position for position in matches if position >= 0), default=-1))
                require(all(position >= 0 for position in positions) and positions == sorted(positions), 'wrong_rank_visible_order')
            if spec['kind'] == 'chart':
                _check_charts(spec, rows, charts, oracle, errors)
            if spec['kind'] == 'quote':
                quote_spec = dict(spec)
                quote_spec['quote_granularity'] = spec.get('quote_granularity') or (
                    'row' if re.search(r'整行|完整.{0,4}行|(?:原文|原表|表格).{0,4}行', case['question'])
                    else observed.get('quote_granularity'))
                _check_quotes(quote_spec, rows, result.get('evidence') or [], body, oracle, errors)
            if spec['kind'] == 'cause':
                review.append('cause_claims_against_original')
                require(bool(result.get('evidence') or result.get('default_reference')), 'cause_original_evidence_missing')
        expected_calcs = oracle.get('calculations', {}).get(case['id'])
        if expected_calcs is None:
            expected_calcs = independent_calculations(case, reference, oracle)
        for calculation in expected_calcs:
            _check_calculation(calculation, result, body, errors)
        permitted_calculations = {(tuple(row['inputs']), '(current-base)/abs(base)*100'
            if row['calculation'] in {'yoy', 'relative_percent'} else 'current-base') for row in expected_calcs}
        require(all((tuple(row.get('inputs') or []), row.get('formula')) in permitted_calculations
                    for row in result.get('derived_facts') or []), 'extra_unrequested_calculation')
    _check_saved_state(case, result, errors)
    errors = sorted(set(errors)); review = sorted(set(review))
    return dict(status='failed' if errors else 'pending_review' if review else 'passed',
                automated_pass=not errors, passed=not errors and not review,
                review_status='pending_review' if review else 'not_required', errors=errors,
                review_required=review, server_outcome=outcome,
                server_verification_preserved=result.get('verification_v3'),
                review_evidence=dict(fact_ids=[fact.get('id') for fact in facts], evidence=result.get('evidence') or [],
                                     answer=body) if review else None)


def _check_calculation(expected, result, body, errors):
    if expected['status'] == 'undefined_zero_base':
        if not re.search(r'基期.{0,12}(?:零|0)|无法计算|未定义', body):
            errors.append('zero_base_not_explained')
        return
    expected_formula = '(current-base)/abs(base)*100' if expected['calculation'] in {'yoy', 'relative_percent'} else 'current-base'
    candidates = [row for row in result.get('derived_facts') or []
                  if row.get('formula') == expected_formula and list(row.get('inputs') or []) == expected['inputs']]
    if len(candidates) != 1:
        errors.append('independent_calculation_missing'); return
    row = candidates[0]
    try:
        if row.get('unit') != expected['unit'] or abs(parse_decimal(row.get('value_exact', row.get('value')))-parse_decimal(expected['value_exact'])) > Decimal('0.000000000001'):
            errors.append('wrong_calculation')
        if not _visible_number(body, expected['value_exact'], expected['unit'], 2):
            errors.append('wrong_or_missing_visible_calculation')
    except (KeyError, ValueError, InvalidOperation):
        errors.append('wrong_calculation')


def _check_charts(spec, facts, charts, oracle, errors):
    expected = {fact['id']: parse_decimal(oracle['cards'][fact['id']]['value_exact'])/FACTORS[spec.get('unit', fact['unit'])] for fact in facts}
    points = {}
    candidates = [chart for chart in charts if chart.get('unit') == spec.get('unit') and chart.get('type', chart.get('chart_type')) == spec.get('chart', 'line')]
    if not candidates:
        errors.append('requested_chart_missing'); return
    for chart in candidates:
        if chart.get('decimals', spec.get('decimals')) != spec.get('decimals'):
            errors.append('wrong_chart_precision')
        for series in chart.get('series') or []:
            native = series.get('fact_ids')
            if native is not None:
                exact = series.get('values_exact') or []
                formatted = series.get('values_formatted') or []
                numeric = series.get('values') or []
                if len(native) != len(exact) or len(native) != len(numeric):
                    errors.append('chart_series_length_mismatch')
                series_points = [dict(fact_id=key, value_exact=value) for key, value in zip(native, exact)]
                for index, (key, value) in enumerate(zip(native, exact)):
                    if key is None:
                        if value is not None or index < len(numeric) and numeric[index] is not None:
                            errors.append('filled_missing_chart_point')
                        continue
                    if key in expected and index < len(formatted) and not _visible_number(formatted[index], oracle['cards'][key]['value_exact'], spec['unit'], spec.get('decimals', 2)):
                        errors.append('wrong_chart_formatted_value')
            else:
                series_points = series.get('points') or []
            for point in series_points:
                if point.get('fact_id') is None:
                    continue
                if point.get('fact_id') in points:
                    errors.append('duplicate_chart_point')
                try:
                    points[point.get('fact_id')] = parse_decimal(point['value_exact'])
                except (KeyError, ValueError, InvalidOperation):
                    errors.append('invalid_chart_point')
    if points != expected:
        errors.append('wrong_chart_points')


def _check_quotes(spec, facts, evidence, body, oracle, errors):
    from src.utils.original_cells import original_literal, normalized_positions
    from src.utils.original_regions import same_physical_bbox
    literal_key = lambda value: normalized_positions(str(value))[0]
    for fact in facts:
        source = fact.get('source') or {}; card = oracle['cards'][fact['id']]
        rows = [row for row in evidence if row.get('fact_id') == fact['id']]
        if not rows:
            errors.append('original_quote_missing'); continue
        for row in rows:
            if row.get('document_version') != source.get('document_version') or row.get('page_start') != source.get('page'):
                errors.append('wrong_quote_location')
            evidence_source = row.get('source') or {}
            paths = [row[key] for key in ('paper_path',) if row.get(key)]
            paths += [evidence_source['path']] if evidence_source.get('path') else []
            if any(str(path).replace('\\', '/') != str(source.get('source_path')).replace('\\', '/') for path in paths):
                errors.append('wrong_quote_location')
            for key, expected in [('stock_code', fact['stock_code']), ('report_year', fact['year']),
                                  ('report_period', fact['period']), ('page_end', source.get('page'))]:
                if row.get(key) is not None and row[key] != expected:
                    errors.append('wrong_quote_location')
            if evidence_source.get('source_sha256') not in {None, source.get('source_sha256')}:
                errors.append('wrong_quote_location')
            if spec.get('quote_mode') == 'literal':
                value_literal = row.get('value_literal') or ''
                text = row.get('text') or ''
                original_region = card.get('original_region_text', card.get('original_row_text', ''))
                expected_literal = str(source.get('raw_value', '')).replace('%', '').replace('％', '')
                if (not value_literal or literal_key(value_literal).replace('%', '') != literal_key(expected_literal)
                        or original_literal(original_region, value_literal.replace('%', '').replace('％', ''), numeric=True) is None):
                    errors.append('fabricated_quote')
                if (not text or original_literal(text, value_literal, numeric=True) is None
                        or original_literal(body, value_literal, numeric=True) is None):
                    errors.append('requested_literal_quote_missing_from_body')
                if not re.search(r'第\s*'+str(source.get('page'))+r'\s*页', body):
                    errors.append('literal_quote_page_missing_from_body')
                if not _visible_unit(source.get('raw_unit', ''), body):
                    errors.append('native_quote_unit_missing_from_body')
                if spec.get('quote_granularity') == 'row':
                    physical_row = card.get('original_table_row') or {}
                    original_row = physical_row.get('row_text') or ''
                    if not original_row:
                        errors.append('original_row_not_independently_verified')
                    elif (row.get('granularity') != 'row' or literal_key(row.get('row_literal') or '') != literal_key(original_row)
                          or literal_key(original_row) not in literal_key(text)
                          or literal_key(original_row) not in literal_key(body)):
                        errors.append('requested_original_row_missing_or_changed')
                    if row.get('row_bbox') is not None and not same_physical_bbox(row['row_bbox'], physical_row.get('row_bbox') or []):
                        errors.append('wrong_quote_row_rectangle')


def _check_saved_state(case, result, errors):
    state = result.get('dialogue_state') or {}; conditions = state.get('conditions') or {}
    if case.get('expected_state'):
        errors.extend('saved_state_'+error for error in condition_errors(case['expected_state'], conditions))
    requested = case['expect'].get('session_preferences') or {}
    actual = {row.get('field'): row.get('value') for row in state.get('session_preferences') or [] if row.get('lifetime') == 'session'}
    for field, value in requested.items():
        if actual.get(field) != value:
            errors.append('saved_session_preference_'+field)


def parse_sse(lines):
    kind, data = '', []
    for raw in lines:
        line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        if line is None or line.startswith(':'):
            continue
        if not line:
            if data:
                yield dict(type=kind or 'message', data=json.loads('\n'.join(data)))
                data = []
            kind = ''
        elif line.startswith('event:'):
            kind = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
    if data:
        yield dict(type=kind or 'message', data=json.loads('\n'.join(data)))


class JavaSSEClient:
    """The v3_native_client Java protocol, with in-memory credentials and recovery.

    Unlike the QA helper this never registers a user or writes credentials. The
    server owns history/state; the client sends only question, session and ID.
    """
    def __init__(self, base, username, password, *, transport=None, pause=time.sleep, timeout=300):
        import requests
        self.base = base.rstrip('/'); self.http = transport or requests.Session()
        self.pause, self.timeout = pause, timeout; self._tokens = None
        pair = self.call('POST', '/api/auth/login', json=dict(username=username, password=password), auth=False)
        from src.utils.java_tokens import RotatingJavaTokens
        self._tokens = RotatingJavaTokens(self.http,self.base,pair)

    def _request(self, method, path, *, allow_refresh=True, **kwargs):
        import requests
        from src.utils.java_tokens import JavaSessionExpired
        timeout=kwargs.pop('timeout',(12,300));refreshed=False
        for attempt in range(3):  # At most two connectivity retries; same request ID.
            try:
                response = self.http.request(method, self.base+path, timeout=timeout, **kwargs)
                if response.status_code==401 and allow_refresh and self._tokens is not None and not refreshed:
                    response.close();refreshed=True
                    try:self._tokens.renew()
                    except JavaSessionExpired:raise RunStopped('authentication_refresh_failed') from None
                    response=self.http.request(method,self.base+path,timeout=timeout,**kwargs)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise PendingSubmission('bounded_connectivity_retries_exhausted') from None
                self.pause(min(2**attempt, 2)); continue
            if response.status_code in {401, 403}:
                response.close(); raise RunStopped('authentication_or_authorization')
            if response.status_code == 402:
                response.close(); raise RunStopped('provider_balance')
            if response.status_code == 429:
                try:
                    code = response.json().get('code')
                except ValueError:
                    code = None
                response.close()
                if code == 42901:
                    raise RunStopped('daily_quota')
                raise RunStopped('rate_limit')
            if response.status_code in {502, 503, 504} and attempt < 2:
                response.close(); self.pause(min(2**attempt, 2)); continue
            response.raise_for_status()
            return response
        raise PendingSubmission('bounded_connectivity_retries_exhausted')

    def call(self, method, path, auth=True, **kwargs):
        response = self._request(method, path, allow_refresh=auth, **kwargs)
        try:
            value = response.json()
        finally:
            response.close()
        if value.get('code') not in {0, '0', 200, '200'}:
            code = value.get('code')
            raise RunStopped('authentication' if code in {401, 40100, 40300} else 'daily_quota' if code == 42901 else 'java_rejected_request')
        return value.get('data')

    def turn(self, question, uid=None, client_id=None, **kwargs):
        if not uid or not client_id:
            raise ValueError('Persist session and client request identity before submitting')
        started = time.monotonic(); events = []
        body = dict(question=question, sessionUid=uid, clientRequestId=client_id)
        task = self.call('GET', '/api/chat/tasks/recover', params=dict(clientRequestId=client_id))
        if task is None:
            response = None
            try:
                response = self._request('POST', '/api/chat/stream', json=body, stream=True)
                response.encoding = 'utf-8'
                for event in parse_sse(response.iter_lines(decode_unicode=True, chunk_size=1)):
                    events.append(event)
                    if event['type'] == 'session':
                        data = event['data']
                        if data.get('session_uid') != uid:
                            raise FreezeMismatch('SSE changed the requested session')
                        task = dict(task_id=data['task_id'])
                    if event['type'] == 'done':
                        break
            except RunStopped:
                raise
            except (ValueError, OSError) as exc:
                # Save the observed SSE in the eventual raw response. Never reroll.
                events.append(dict(type='local_transport_observation', data=dict(error_type=type(exc).__name__)))
            finally:
                if response is not None:
                    response.close()
            if task is None:
                task = self.call('GET', '/api/chat/tasks/recover', params=dict(clientRequestId=client_id))
        if not task or not task.get('task_id'):
            raise PendingSubmission('No task identity; resume exact persisted request ID')
        deadline = time.monotonic()+self.timeout
        while time.monotonic() < deadline:
            task = self.call('GET', '/api/chat/tasks/'+task['task_id'])
            if task.get('status') in {'completed', 'cancelled', 'failed'}:
                break
            self.pause(1)
        else:
            raise PendingSubmission('Remote task remains active; resume exact persisted request ID')
        if task.get('session_uid') not in {None, uid} or task.get('client_request_id') not in {None, client_id} or task.get('question') not in {None, question}:
            raise FreezeMismatch('Recovered task identity differs from the frozen submission')
        return dict(question=question, session_uid=uid, client_request_id=client_id, task_id=task['task_id'],
                    seconds=round(time.monotonic()-started, 3), events=events, task=task)


def _messages(detail):
    return [dict(role=row.get('role'), content=row.get('content'),
                 dialogue_state=(row.get('metadata') or {}).get('dialogue_state')) for row in detail.get('messages') or []]


def saved_history_errors(case, run, detail, before):
    errors = []; messages = _messages(detail); prefix = _messages(before)
    if messages[:len(prefix)] != prefix:
        errors.append('previous_saved_history_changed')
    result = (run.get('task') or {}).get('result') or {}; answer = _body(result)
    if len(messages) != len(prefix)+2:
        errors.append('saved_history_turn_count_changed')
    if len(messages) < 2 or messages[-2].get('role') != 'user' or messages[-2].get('content') != case['question']:
        errors.append('saved_user_turn_missing')
    if not messages or messages[-1].get('role') != 'assistant' or messages[-1].get('content') != answer:
        errors.append('saved_assistant_turn_missing')
    if result.get('dialogue_state') is not None and (not messages or messages[-1].get('dialogue_state') != result['dialogue_state']):
        errors.append('saved_actual_dialogue_state_changed')
    return errors


class RunLedger:
    """Exclusive first-answer files and append-only raw records; derived reports may refresh."""
    def __init__(self, folder, fingerprint, cases, *, resume=False):
        self.folder = Path(folder); self.cases = cases
        self.definition = dict(schema=1, fingerprint=fingerprint, cases_sha256=value_digest(cases))
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder/'freeze.json'
        if path.exists():
            if not resume:
                raise FileExistsError('Existing run requires --resume')
            if read(path) != self.definition:
                raise FreezeMismatch('Source/data/model/case freeze differs; use a new output directory')
        else:
            if resume:
                raise FreezeMismatch('Cannot resume without frozen input definition')
            if any((self.folder/name).exists() for name in ('answers', 'scores', 'requests', 'records.jsonl')):
                raise FreezeMismatch('Unidentified old results exist in output')
            write_new(path, self.definition)
        missing = False
        for case in cases:
            raw = self.raw(case)
            if raw is None:
                missing = True
            elif missing:
                raise FreezeMismatch('Immutable answers must be an ordered question prefix')
            self.score(case)
        self.sessions()
        self._sync_records()

    def path(self, category, case, suffix='.json'):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', case['id']):
            raise ValueError('Unsafe case identifier')
        return self.folder/category/(case['id']+suffix)

    def raw(self, case):
        path = self.path('answers', case)
        if not path.exists():
            return None
        row = read(path)
        if row.get('case') != case or row.get('sha256') != value_digest(dict(case=case, run=row.get('run'))):
            raise FreezeMismatch('Immutable first answer changed: '+case['id'])
        return row['run']

    def save_raw(self, case, run):
        row = dict(case=case, run=run)
        write_new(self.path('answers', case), dict(row, sha256=value_digest(row)))
        self.export()

    def score(self, case):
        path = self.path('scores', case)
        if not path.exists():
            return None
        row = read(path); raw = self.raw(case)
        if raw is None or row.get('answer_sha256') != value_digest(raw) or row.get('sha256') != value_digest(row.get('score')):
            raise FreezeMismatch('First score or its answer binding changed: '+case['id'])
        return row['score']

    def save_score(self, case, score):
        raw = self.raw(case)
        if raw is None:
            raise ValueError('Save raw answer before scoring')
        write_new(self.path('scores', case), dict(answer_sha256=value_digest(raw), score=score, sha256=value_digest(score)))
        self.export()

    def sessions(self):
        sessions, reverse = {}, {}
        for case in self.cases:
            key = case.get('group') or case['id']
            intent_path = self.path('requests', case)
            raw = self.raw(case)
            uid = (raw or {}).get('session_uid') or (read(intent_path).get('session_uid') if intent_path.exists() else None)
            if not uid:
                continue
            if (key in sessions and sessions[key] != uid) or (uid in reverse and reverse[uid] != key):
                raise FreezeMismatch('Conversation sessions are reused or changed')
            sessions[key] = uid; reverse[uid] = key
        return sessions

    def intent(self, case, client):
        path = self.path('requests', case)
        if path.exists():
            row = read(path)
            if row.get('case') != case:
                raise FreezeMismatch('Pending request question changed')
            return row
        uid = self.sessions().get(case.get('group') or case['id'])
        if uid is None:
            uid = client.call('POST', '/api/chat/sessions')['sessionUid']
        before = client.call('GET', '/api/chat/sessions/'+uid)
        if not case.get('group') or case.get('turn', 1) == 1:
            if _messages(before):
                raise FreezeMismatch('A new isolated session is not empty')
        row = dict(case=case, session_uid=uid, client_request_id=uuid.uuid4().hex, before=before)
        write_new(path, row)
        self.sessions()
        return row

    def _sync_records(self):
        path = self.folder/'records.jsonl'
        rows = []
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    raise FreezeMismatch('Append-only records contain a partial or modified line') from None
        expected = []
        for case in self.cases:
            raw = self.raw(case)
            if raw is not None:
                expected.append(dict(case=case, run=raw, answer_sha256=value_digest(raw), score_file='scores/'+case['id']+'.json'))
        if rows != expected[:len(rows)]:
            raise FreezeMismatch('Append-only first-result records changed')
        if len(rows) < len(expected):
            with path.open('a', encoding='utf-8', newline='\n') as stream:
                for row in expected[len(rows):]:
                    stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                    stream.flush(); os.fsync(stream.fileno())

    def export(self):
        self._sync_records()
        rows = [dict(case=case, run=self.raw(case), score=self.score(case)) for case in self.cases if self.raw(case) is not None]
        scored = [row for row in rows if row['score'] is not None]
        passed = sum(row['score'].get('passed') is True for row in scored)
        pending = sum(row['score'].get('review_status') == 'pending_review' for row in scored)
        summary = dict(formal_total=len(self.cases), answers_saved=len(rows), scored=len(scored), passed=passed,
                       automated_passed=sum(row['score'].get('automated_pass') is True for row in scored),
                       pending_review=pending, failed=sum(row['score'].get('status') == 'failed' for row in scored),
                       oracle_missing=sum(row['score'].get('status') == 'oracle_missing' for row in scored),
                       accuracy=passed/len(self.cases) if self.cases else 0,
                       complete_run=len(rows) == len(self.cases) and len(scored) == len(self.cases),
                       no_accuracy_threshold=True, entry='real_java_sse',
                       partitions={side: dict(total=sum(case['partition'] == side for case in self.cases),
                           saved=sum(row['case']['partition'] == side for row in rows),
                           passed=sum(row['case']['partition'] == side and row['score'].get('passed') is True for row in scored))
                           for side in sorted({case['partition'] for case in self.cases})})
        replace_derived(self.folder/'summary.json', json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
        replace_derived(self.folder/'checkpoint.json', json.dumps(dict(freeze_sha256=value_digest(self.definition),
                         completed_ids=[row['case']['id'] for row in rows], sessions=self.sessions()), ensure_ascii=False, indent=2)+'\n')
        cards = []
        for row in rows:
            case, run = row['case'], row['run']; body = _body((run.get('task') or {}).get('result') or {})
            text_path, trace_path = self.path('text', case, '.txt'), self.path('alltrace', case)
            text_value = case['question']+'\n\n'+body+'\n'
            if not text_path.exists():
                write_new_text(text_path, text_value)
            elif text_path.read_text(encoding='utf-8') != text_value:
                raise FreezeMismatch('Saved first answer text changed')
            if not trace_path.exists():
                write_new(trace_path, run)
            elif read(trace_path) != run:
                raise FreezeMismatch('Saved full first trace changed')
            cards.append('<article id="'+html.escape(case['id'], quote=True)+'"><h2>'+html.escape(case['id'])+
                         ' · '+html.escape(case['partition'])+'</h2><p>'+html.escape(case['question'])+'</p><pre>'+
                         html.escape(body)+'</pre><p>'+html.escape(json.dumps(row['score'], ensure_ascii=False))+
                         '</p><details><summary>完整首发结果和服务端状态</summary><pre>'+
                         html.escape(json.dumps(row, ensure_ascii=False, indent=2))+'</pre></details></article>')
        page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>NEW公司200轮首发回答</title><style>body{max-width:1100px;margin:2rem auto;padding:1rem;font:16px system-ui}article{border-top:1px solid #ddd;padding:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>NEW公司财报首发回答</h1><p>原因和概念正文待独立复核，pending_review不计通过；没有通过率门槛。图表完整数据见每题追踪记录。</p>'+''.join(cards)+'</html>'
        replace_derived(self.folder/'answers.html', page)
        return summary


def _stop_reason(run):
    task = run.get('task') or {}; result = task.get('result') or {}
    values = [task.get('error'), *(result.get('outcome') or {}).get('reason_codes', [])]
    text = ' '.join(str(value) for value in values).lower()
    if re.search(r'auth|unauthorized|invalid.?api.?key', text):
        return 'provider_authentication'
    if re.search(r'balance|insufficient|quota|credit', text):
        return 'provider_balance_or_quota'
    return None


def run_cases(cases, ledger, client, reference, oracle, *, model_version, verify=lambda: None, interval=0):
    for case in cases:
        verify()
        raw = ledger.raw(case)
        if raw is None:
            intent = ledger.intent(case, client)
            # The before snapshot is server history. Never inject oracle/expected state.
            current = client.call('GET', '/api/chat/sessions/'+intent['session_uid'])
            prefix = _messages(intent['before'])
            if _messages(current)[:len(prefix)] != prefix:
                raise FreezeMismatch('Saved previous state changed before pending turn')
            if case.get('group') and case.get('turn', 1) > 1:
                previous = [prior for prior in cases if prior.get('group') == case['group'] and prior.get('turn') == case['turn']-1]
                if len(previous) != 1 or not ledger.raw(previous[0]):
                    raise FreezeMismatch('Cannot run a dialogue turn without the actual saved previous answer')
                prior = ledger.raw(previous[0]); prior_state = ((prior.get('task') or {}).get('result') or {}).get('dialogue_state')
                if prior_state is not None and (not prefix or prefix[-1].get('dialogue_state') != prior_state):
                    raise FreezeMismatch('Server history does not contain the previous actual dialogue state')
            try:
                raw = client.turn(case['question'], uid=intent['session_uid'], client_id=intent['client_request_id'])
            except RunStopped:
                raise
            except Exception as exc:
                write_new(ledger.folder/'incidents'/(str(time.time_ns())+'.json'), dict(case_id=case['id'],
                          client_request_id=intent['client_request_id'], error_type=type(exc).__name__,
                          status='pending; resume the same persisted request identity'))
                raise PendingSubmission(case['id']) from None
            ledger.save_raw(case, raw)  # Immutable before any scorer/history/review work.
        if ledger.score(case) is None:
            verdict = score_case(case, raw, reference, oracle, model_version=model_version)
            intent = read(ledger.path('requests', case))
            history = client.call('GET', '/api/chat/sessions/'+raw['session_uid'])
            history_path = ledger.path('history', case)
            if not history_path.exists():
                write_new(history_path, history)
            else:
                history = read(history_path)
            errors = saved_history_errors(case, raw, history, intent['before'])
            verdict['errors'] = sorted(set(verdict['errors']+errors))
            if errors:
                verdict.update(passed=False, automated_pass=False, status='failed')
            verdict['server_saved_history_checked'] = not errors
            ledger.save_score(case, verdict)
        reason = _stop_reason(raw)
        if reason:
            raise RunStopped(reason)
        if (raw.get('task') or {}).get('saved') is not True:
            raise RunStopped('server_result_not_saved')
        ledger.export()
        if interval:
            time.sleep(interval)
    verify()
    return ledger.export()


def source_fingerprint(root=ROOT):
    root = Path(root); files = {}
    extensions = {'.py', '.java', '.json', '.xml', '.yml', '.yaml', '.ts', '.tsx', '.vue', '.ps1', '.sql'}
    for name in ('src', 'backend-java/src', 'frontend/src', 'eval', 'config', 'shared', 'scripts', 'database'):
        for path in sorted((root/name).rglob('*')):
            if path.is_file() and path.suffix.lower() in extensions and '__pycache__' not in path.parts:
                files[path.relative_to(root).as_posix()] = digest(path)
    for name in ('requirements.txt', 'requirements.lock.txt', 'backend-java/pom.xml', 'frontend/package.json', 'frontend/package-lock.json'):
        if (root/name).is_file():
            files[name] = digest(root/name)
    return files


def _public_settings(value):
    """Freeze public configuration without persisting nested credentials."""
    def check(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or re.search(r'key|password|secret|token|authorization|credential', key, re.I):
                    raise ValueError('Freeze settings take public configuration only, never credentials')
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)
    if not isinstance(value, dict):
        raise ValueError('Public freeze settings must be a JSON object')
    check(value)
    return copy.deepcopy(value)


def _fingerprint(reference, oracle, *, root, cases_path, model_version, model_policy, partition,
                 runtime_snapshot=None):
    if not model_version or not isinstance(model_version, str):
        raise ValueError('An explicit private model version/name is required')
    policy = _public_settings(model_policy or {})
    runtime = _public_settings(runtime_snapshot) if runtime_snapshot is not None else None
    if runtime is not None and runtime.get('version', reference['version']) != reference['version']:
        raise FreezeMismatch('Selected runtime and original-based oracle name different releases')
    return dict(source=source_fingerprint(root), artifacts=reference['artifacts'], originals=reference['documents'],
                data_version=reference['version'], suite_sha256=digest(cases_path), questions_sha256=QUESTIONS_SHA256,
                oracle_sha256=value_digest(oracle), model=dict(version=model_version, settings=policy),
                partition=partition, runtime_snapshot=runtime)


def preparation_summary(cases, reference, oracle):
    """Coverage is independent of answers; unavailable data and unfinished audits stay distinct."""
    gaps = oracle.get('data_gaps', [])
    by_reason = Counter(gap['reason'] for gap in gaps)
    affected = {}
    for gap in gaps:
        identifiers = gap.get('case_ids', []) or ([gap['case_id']] if gap.get('case_id') else [])
        affected.setdefault(gap['reason'], set()).update(identifiers)
    all_affected = set().union(*affected.values()) if affected else set()
    review_cases = [case['id'] for case in cases if case['expect'].get('manual_review')
        or case['expect']['kind'] == 'concept'
        or any(spec['kind'] in {'cause', 'concept'} for spec in case['expect']['goal_expectations'])]
    selected_ids = {case['id'] for case in cases}
    cards = oracle.get('cards', {})
    inventory_gaps = reference.get('inventory_gaps', [])
    return dict(schema=1, data_version=reference['version'], questions_sha256=QUESTIONS_SHA256,
        formal_total=len(cases), reports_available=len(reference['reports']),
        report_inventory_missing=inventory_gaps, report_inventory_missing_count=len(inventory_gaps),
        candidate_fact_statuses=dict(Counter(fact.get('status', 'unspecified') for fact in reference['facts'].values())),
        original_verified_direct_facts=sum(bool(card.get('source')) for card in cards.values()),
        derived_from_original_verified_facts=sum(not card.get('source') for card in cards.values()),
        gap_counts=dict(by_reason),
        unavailable_metric_cases=len(affected.get('metric_not_extracted', set()) & selected_ids),
        original_audit_incomplete_cases=len(affected.get('original_audit_incomplete', set()) & selected_ids),
        original_revalidation_failed_cases=len(affected.get('original_revalidation_failed', set()) & selected_ids),
        cases_with_oracle_gaps=len(all_affected & selected_ids),
        cases_without_oracle_gaps=len(selected_ids-all_affected),
        independent_review_required_cases=len(review_cases), independent_review_required_ids=review_cases,
        original_oracle_complete=not gaps and not inventory_gaps,
        no_cloud_request_made=True, no_accuracy_threshold=True,
        gap_meaning={'metric_not_extracted': 'Required candidate metric is unavailable; case remains in the denominator',
            'original_audit_incomplete': 'Candidate exists but original cell/scope audit is unfinished; correctness is not established',
            'original_revalidation_failed': 'Independent original or lineage revalidation failed; inspect the recorded reason'},
        review_policy=oracle.get('review_policy'))


def prepare(candidate_artifacts, output, *, root=ROOT, cases_path=CASE_FILE,
            model_version, model_policy=None, partition='all',record_data_gaps=False, runtime_snapshot=None):
    """Offline public API. Freeze all questions and original-based expectations.

    Returns the output directory. Raises OracleMissing with explicit coverage gaps
    before constructing a client or contacting any service.
    """
    if partition not in {'all', 'development', 'holdout'}:
        raise ValueError('Invalid frozen partition')
    cases = load_cases(cases_path); output = Path(output); output.mkdir(parents=True, exist_ok=True)
    questions = dict(suite_sha256=SUITE_SHA256, questions_sha256=QUESTIONS_SHA256, cases=cases)
    question_path = output/'questions.freeze.json'
    if question_path.exists():
        if read(question_path) != questions:
            raise FreezeMismatch('Questions were changed after upfront freeze')
    else:
        write_new(question_path, questions)
    try:
        reference = load_release(candidate_artifacts, root=root,require_cohort=not record_data_gaps)
        oracle_path = output/'oracle.json'
        if oracle_path.exists():
            oracle = read(oracle_path)
            # Recheck original bytes and immutable release; no values from answers.
            if (oracle.get('schema') != 2 or oracle.get('calibration_version') != ORACLE_CALIBRATION_VERSION
                or oracle.get('reference_sha256') != _oracle_reference_digest(reference)
                or oracle.get('questions_sha256') != QUESTIONS_SHA256 or oracle.get('data_version') != reference['version']
                or any(digest(local_path(path, root)) != sha for path, sha in oracle.get('documents', {}).items())):
                raise FreezeMismatch('Original-based oracle release or PDF bytes changed')
            if oracle.get('data_gaps') and not record_data_gaps:
                raise OracleMissing(oracle['data_gaps'])
        else:
            oracle = build_oracle(cases, reference, root=root,record_data_gaps=record_data_gaps)
            write_new(oracle_path, oracle)
    except OracleMissing as exc:
        write_new(output/'preparation-gaps'/(str(time.time_ns())+'.json'), dict(status='oracle_missing',
                  questions_sha256=QUESTIONS_SHA256, gaps=exc.gaps, no_cloud_request_made=True))
        raise
    selected = [case for case in cases if partition == 'all' or case['partition'] == partition]
    fingerprint = _fingerprint(reference, oracle, root=root, cases_path=cases_path,
                               model_version=model_version, model_policy=model_policy, partition=partition,
                               runtime_snapshot=runtime_snapshot)
    RunLedger(output, fingerprint, selected, resume=(output/'freeze.json').exists()).export()
    preparation = preparation_summary(selected, reference, oracle)
    preparation_path = output/'preparation.json'
    if preparation_path.exists():
        if read(preparation_path) != preparation:
            raise FreezeMismatch('Frozen preparation coverage changed')
    else:
        write_new(preparation_path, preparation)
    return output


@contextlib.contextmanager
def runner_lock(folder):
    path = Path(folder)/'runner.lock'; path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run(candidate_artifacts, output, *, base_url='http://127.0.0.1:18080', model_version,
        resume=False, username=None, password=None, client=None, root=ROOT, cases_path=CASE_FILE,
        model_policy=None, partition='all', interval=11, verify=lambda: None,record_data_gaps=False,
        runtime_snapshot=None):
    """Public Java/SSE run API; resume only unexecuted/pending frozen requests.

    With ``runtime_snapshot``, ``verify`` must return the current public profile,
    pointer, release, model and index identity. A mismatch stops before a turn.
    """
    output = Path(output)
    if (output/'answers').exists() and not resume:
        raise FileExistsError('First results already exist; use --resume for unexecuted turns')
    with runner_lock(output):
        prepare(candidate_artifacts, output, root=root, cases_path=cases_path,
                model_version=model_version, model_policy=model_policy, partition=partition,record_data_gaps=record_data_gaps,
                runtime_snapshot=runtime_snapshot)
        cases = [case for case in load_cases(cases_path) if partition == 'all' or case['partition'] == partition]
        reference = load_release(candidate_artifacts, root=root,require_cohort=not record_data_gaps); oracle = read(output/'oracle.json')
        fingerprint = _fingerprint(reference, oracle, root=root, cases_path=cases_path,
                                   model_version=model_version, model_policy=model_policy, partition=partition,
                                   runtime_snapshot=runtime_snapshot)
        ledger = RunLedger(output, fingerprint, cases, resume=True)
        # Authenticate only after all originals, questions, source and model are frozen.
        if client is None:
            username = username or os.environ.get('UPLOAD200_USERNAME') or input('Java test username: ')
            password = password or os.environ.get('UPLOAD200_PASSWORD') or getpass.getpass('Java test password: ')
            account = dict(username_sha256=hashlib.sha256(username.encode()).hexdigest(), base_url=base_url.rstrip('/'))
            account_path = output/'account.freeze.json'
            if account_path.exists() and read(account_path) != account:
                raise FreezeMismatch('Resume must use the original Java account and endpoint')
            if not account_path.exists():
                write_new(account_path, account)
            client = JavaSSEClient(base_url, username, password)
            password = None
        def verify_frozen():
            actual_runtime = verify()
            if runtime_snapshot is not None and actual_runtime != fingerprint['runtime_snapshot']:
                raise FreezeMismatch('Active profile, pointer, release, model or index changed after freeze')
            if source_fingerprint(root) != fingerprint['source']:
                raise FreezeMismatch('Source changed after freeze')
            if any(digest(Path(reference['folder'])/name) != sha for name, sha in fingerprint['artifacts'].items()):
                raise FreezeMismatch('Candidate release changed after freeze')
        return run_cases(cases, ledger, client, reference, oracle,
                         model_version=model_version, verify=verify_frozen, interval=interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-artifacts', required=True, help='Local folder/file URI with reports/facts/narratives/manifest')
    parser.add_argument('--output', required=True)
    parser.add_argument('--model-version', required=True, help='Exact model identity expected in server diagnostics')
    parser.add_argument('--model-policy', help='JSON file of public model settings only; no API keys')
    parser.add_argument('--runtime-snapshot', help='JSON file of public runtime profile/release/index identity for offline preparation')
    parser.add_argument('--base', default='http://127.0.0.1:18080')
    parser.add_argument('--partition', choices=('all', 'development', 'holdout'), default='all')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--record-data-gaps',action='store_true',help='Continue with extraction gaps explicitly counted as oracle_missing failures, never expected no_data')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--interval', type=float, default=11, help='Seconds between submitted turns; frozen server rate limit still applies')
    args = parser.parse_args(argv)
    if args.interval < 0 or args.interval > 60:
        parser.error('--interval must be in 0..60 seconds')
    options = dict(model_version=args.model_version, model_policy=read(args.model_policy) if args.model_policy else None,
                   partition=args.partition,record_data_gaps=args.record_data_gaps,
                   runtime_snapshot=read(args.runtime_snapshot) if args.runtime_snapshot else None)
    try:
        if args.prepare_only:
            prepare(args.candidate_artifacts, args.output, **options)
            print(json.dumps(dict(status='frozen_offline', output=str(Path(args.output).resolve()),
                                  **read(Path(args.output)/'preparation.json')), ensure_ascii=False))
        else:
            print(json.dumps(run(args.candidate_artifacts, args.output, base_url=args.base,
                                 resume=args.resume, interval=args.interval, **options), ensure_ascii=False))
    except (OracleMissing, FreezeMismatch, PendingSubmission, RunStopped, FileExistsError) as exc:
        print(json.dumps(dict(status=type(exc).__name__, detail=str(exc)), ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
