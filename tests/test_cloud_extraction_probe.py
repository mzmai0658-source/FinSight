"""Bound the optional real OCR cloud smoke to local output and two generation calls."""
from __future__ import annotations
import importlib
import json
from pathlib import Path

import pytest


def probe():
    assert importlib.util.find_spec('eval.cloud_extraction') is not None
    return importlib.import_module('eval.cloud_extraction')


class Model:
    def __init__(self):
        self.calls = 0
        self.last_diagnostics = {'model': 'deepseek-flash'}

    def chat(self, *args, **kwargs):
        self.calls += 1
        return '{"stock_code":"002082","report_year":2022,"report_period":"FY"}'


def test_generation_bound_counts_attempts_and_preserves_json_policy():
    module = probe()
    model = Model()
    client = module.BoundedClient(model, 2)
    assert client.chat([], temperature=0, json_mode=True)
    assert client.chat([], temperature=0, json_mode=True)
    with pytest.raises(RuntimeError, match='generation_budget'):
        client.chat([], temperature=0, json_mode=True)
    assert model.calls == 2
    assert client.calls == 2
    assert len(client.diagnostics) == 2


def test_preview_cannot_send_a_generation():
    module = probe()
    model = Model()
    client = module.BoundedClient(model, 0)
    with pytest.raises(RuntimeError, match='generation_budget'):
        client.chat([], json_mode=True)
    assert model.calls == 0


def test_ocr_smoke_uses_real_cache_and_does_not_initialize_database(tmp_path, monkeypatch):
    module = probe()
    # No synthetic financial source is passed off as real: this fixture checks only driver boundaries.
    sample = {'report': {'stock_code': '002082', 'year': 2022, 'period': 'FY'},
              'pdf': tmp_path/'test.pdf', 'cache': tmp_path/'cache.json',
              'pdf_sha256': 'pdf-hash', 'cache_sha256': 'cache-hash',
              'content': '002082 2022年年度报告', 'chunks': [{'text': 'real cache chunk'}]}
    monkeypatch.setattr(module, 'load_sample', lambda *args: sample)

    class Worker:
        def __init__(self, llm, **kwargs):
            self.llm = llm

        def _normalize_chunk_for_processing(self, chunk): return chunk
        def _should_accept_chunk(self, chunk): return True
        def _extract_metadata_with_llm(self, content):
            return json.loads(self.llm.chat([], temperature=0, json_mode=True))
        def _extract_chunk_data(self, chunk): return {'eps': .03}, 'rules'
        def _save_to_db(self, *args): pytest.fail('The probe must never save to the finance database')

    monkeypatch.setattr(module, 'ETLWorker', Worker)
    model = Model()
    preview = module.run_probe(tmp_path, tmp_path, generate=False, client=model)
    assert preview['generation_calls'] == 0
    assert model.calls == 0
    live = module.run_probe(tmp_path, tmp_path, generate=True, client=model)
    assert live['generation_calls'] == 1
    assert live['metadata_matches_report'] is True
    assert live['database_writes'] == 0
    assert live['index_writes'] == 0
    assert live['table_route'] == 'rules'


def test_smoke_refuses_an_unverified_pdf_or_cache_hash(tmp_path):
    module = probe()
    audit = tmp_path/'audit'
    audit.mkdir()
    report = {'stock_code': '002082', 'year': 2022, 'period': 'FY',
              'source_path': 'data_root/changed.pdf', 'document_version': 'not-the-actual-pdf',
              'cache_sha256': 'old-cache'}
    pdf = tmp_path/report['source_path']
    pdf.parent.mkdir()
    pdf.write_bytes(b'modified source')
    (audit/'reports.json').write_text(json.dumps([report]), encoding='utf-8')
    with pytest.raises(ValueError, match='PDF.*hash'):
        module.load_sample(audit, tmp_path)
