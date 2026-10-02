"""作品说明：读取已核对的版本化字段来源，不伪造位置。"""
import hashlib
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _identity(data: dict) -> tuple:
    year = data.get('report_year', '')
    try:
        numeric = float(year)
        if numeric.is_integer(): year = int(numeric)
    except (TypeError, ValueError):
        pass
    return str(data.get('stock_code','')), str(year), str(data.get('report_period',''))


def attach_provenance(rows: list[dict], lineage: dict, scope: dict, connection=None) -> list[dict]:
    manifest = os.getenv('FINANCIAL_FACTS_MANIFEST', '').strip()
    if manifest:
        path = Path(manifest)
        path = path if path.is_absolute() else ROOT / path
        try:
            facts = json.loads(path.read_text(encoding='utf-8')).get('facts', [])
        except (OSError, ValueError):
            return rows
    elif connection is not None:
        from src.etl.provenance_store import read_facts
        from sqlalchemy.exc import SQLAlchemyError
        try:
            facts = read_facts(connection,[{**scope,**row} for row in rows])
        except SQLAlchemyError:
            # 作品说明：旧数据库回答保持兼容，但不能编造来源。
            return rows
    else:
        return rows
    by_key = {}
    source_cache = {}
    for fact in facts:
        key = _identity(fact) + (str(fact.get('table','')), str(fact.get('field','')))
        by_key[key] = fact
    for row in rows:
        identity = {**scope, **row}
        evidence = {}
        for output, info in lineage.items():
            key = _identity(identity) + (info['table'], info['field'])
            fact = by_key.get(key)
            if not fact:
                continue
            try:
                if not math.isclose(float(row[output]), float(fact['value']), rel_tol=1e-10, abs_tol=1e-8):
                    continue
                source = (ROOT / fact['source_path']).resolve()
                if not source.is_relative_to(ROOT / 'demo') and not source.is_relative_to(ROOT / 'data_root'):
                    continue
                if source not in source_cache:
                    with source.open('rb') as stream:
                        source_cache[source] = hashlib.file_digest(stream, 'sha256').hexdigest()
                if source_cache[source] != fact['source_sha256']:
                    continue
                evidence[output] = {k: fact[k] for k in ('document_id', 'document_version', 'source_path', 'source_sha256', 'page_start', 'page_end', 'table_name', 'raw_value', 'unit_multiplier', 'statement_scope', 'location_precision', 'human_verified') if k in fact}
                evidence[output]['status'] = 'source_located'
            except (OSError, ValueError, TypeError, KeyError):
                continue
        if evidence:
            row['_provenance'] = evidence
    return rows
