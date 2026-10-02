"""作品说明：使用相同条件比较本地模型，保留模型及服务故障和完整证据。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', required=True)
    parser.add_argument('--modes', nargs='+', default=['agent', 'tool_baseline'],
                        choices=['bare', 'tool_baseline', 'agent', 'no_verifier', 'no_rerank'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--env-file', default='.env.demo')
    parser.add_argument('--reasoning-effort', default='none', choices=['none', 'low', 'medium', 'high', 'max'])
    parser.add_argument('--max-tokens', type=int, default=1024)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=True)
    os.environ.update(LLM_PROVIDER='ollama', OLLAMA_REASONING_EFFORT=args.reasoning_effort,
                      LLM_MAX_TOKENS=str(args.max_tokens), EMBEDDING_PROVIDER='bge_local',
                      EMBEDDING_DEVICE='cpu', HF_HUB_OFFLINE='1')
    # 作品说明：所有请求保持本机执行，避免其他应用配置的通用模型环境变量改变目标服务。
    os.environ['LLM_BASE_URL'] = os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434/v1')
    from eval.run_eval import load_dataset, run_evaluation, write_outputs, rescore_records
    from eval.experiment import RealRuntime, build_manifest
    from src.agent.llm_client import LLMClient
    from src.agent.rag_tool import RAGTool
    import requests

    calls: list[dict] = []
    original_session = LLMClient._build_session

    def instrumented_session():
        session = original_session()
        post = session.post

        def measured_post(url, **kwargs):
            start = time.monotonic()
            entry = {'stream': bool(kwargs.get('stream')), 'status': None}
            calls.append(entry)
            try:
                response = post(url, **kwargs)
                entry['status'] = response.status_code
                # 作品说明：此处流式计时只到响应头返回，不代表完整生成耗时。
                entry['response_headers_seconds'] = round(time.monotonic() - start, 3)
                if not kwargs.get('stream'):
                    try:
                        body = response.json()
                        entry['usage'] = body.get('usage')
                        entry['finish_reasons'] = [c.get('finish_reason') for c in body.get('choices', [])]
                        if response.status_code >= 400:
                            entry['error'] = body.get('error')
                    except ValueError:
                        entry['error'] = f'Non-JSON HTTP {response.status_code}'
                return response
            except requests.RequestException as exc:
                entry['error'] = type(exc).__name__
                raise

        session.post = measured_post
        return session

    LLMClient._build_session = staticmethod(instrumented_session)
    cases = load_dataset()
    selected = cases[:args.limit] if args.limit else cases
    args.output.mkdir(parents=True, exist_ok=True)
    index = {'models': args.models, 'modes': args.modes, 'case_ids': [c['id'] for c in selected],
             'reasoning_effort': args.reasoning_effort, 'max_tokens': args.max_tokens,
             'embedding_device': 'cpu', 'order': 'model blocks; modes alternate within each case',
             'cold_start': 'model and embedding warmup excluded from case latency', 'runs': []}
    index_path = args.output / 'comparison.json'
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    root_url = os.environ['LLM_BASE_URL'].rstrip('/').removesuffix('/v1')

    for model_index, model in enumerate(args.models, 1):
        os.environ['LLM_MODEL'] = os.environ['OLLAMA_MODEL'] = model
        folder = args.output / model.replace(':', '_').replace('/', '_')
        if (folder / 'report.jsonl').exists():
            raise FileExistsError(f'Refusing to overwrite existing experiment: {folder}')
        runtime = RealRuntime()
        warmed = runtime.llm.chat([{'role': 'user', 'content': 'Reply with exactly OK.'}])
        if not warmed:
            raise RuntimeError(f'Model warmup failed: {model}')
        RAGTool().run('星河医药2024年业务变化原因', top_k=1)
        info = runtime.runtime_info()
        info.update(embedding_device='cpu', max_retries=runtime.llm.max_retries,
                    timeout_seconds=runtime.llm.timeout, comparison_runner='eval/compare_models.py')
        actual_context = (info.get('loaded_model') or {}).get('context_length')
        if actual_context != info['num_ctx']:
            raise RuntimeError(f'Loaded context {actual_context} differs from configured {info["num_ctx"]}')
        manifest = build_manifest(cases, info, False)
        records = []
        for case_index, case in enumerate(selected, 1):
            modes = args.modes if case_index % 2 else list(reversed(args.modes))
            for mode in modes:
                calls.clear()
                print(f'MODEL {model_index}/{len(args.models)} {model} CASE {case_index}/{len(selected)} {case["id"]} {mode}', flush=True)
                record = run_evaluation([case], mode=mode, runtime=runtime, manifest=manifest)[0]
                record['llm_diagnostics'] = list(calls)
                record['model_http_errors'] = sum(bool(c.get('error')) or (c.get('status') or 0) >= 400 for c in calls)
                record['model_truncated_calls'] = sum('length' in c.get('finish_reasons', []) for c in calls)
                records.append(record)
                write_outputs(records, folder / 'report.md', len({r['case_id'] for r in records}), manifest)
                print('RESULT', case['id'], mode, record['latency_seconds'], record['scores'], 'HTTP_ERRORS', record['model_http_errors'], flush=True)
        replay = rescore_records(cases, records)
        if [r['scores'] for r in replay] != [r['scores'] for r in records]:
            raise AssertionError('Independent replay changed scores')
        replay_manifest = {**manifest, 'replay': {'method': 'rescore saved complete in-memory records without model'}}
        write_outputs(replay, folder / 'replay.md', len(selected), replay_manifest)
        index['runs'].append({'model': model, 'folder': str(folder), 'records': len(records),
                              'manifest_fingerprint': manifest['fingerprint'], 'complete': True})
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'COMPARISON COMPLETE: {index_path}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
