"""作品说明：检查本机服务就绪状态；可选端到端探测使用当前 v3 接口。"""
import argparse,json,sys
from pathlib import Path
import requests
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env',default='.env.integration')
    p.add_argument('--end-to-end',action='store_true');p.add_argument('--check-embedding',action='store_true')
    p.add_argument('--base',default='http://127.0.0.1:18080');args=p.parse_args()
    load_dotenv(ROOT/args.env,override=True)
    from src.agent.providers import resolve_chat_provider
    config=resolve_chat_provider();root=config.base_url.removesuffix('/v1')
    version=requests.get(root+'/api/version',timeout=5);version.raise_for_status()
    response=requests.get(root+'/api/tags',timeout=5);response.raise_for_status()
    ready=config.model in {m['name'] for m in response.json()['models']}
    print(json.dumps(dict(model=config.model,installed=ready,ollama=version.json(),thinking=False,max_calls=6,call_seconds=60),ensure_ascii=False))
    if not ready:return 1
    if args.check_embedding:
        from src.agent.providers import resolve_embedding_function
        vector=resolve_embedding_function('query').embed_query('财务原文索引检查')
        print(json.dumps(dict(embedding_dimensions=len(vector),ready=bool(len(vector)))))
    if args.end_to_end:
        from eval.v3_native_client import NativeClient
        run=NativeClient(args.base).turn('同仁堂2024年归母净利润是多少？');result=run['task'].get('result') or {}
        print(json.dumps(dict(outcome=result.get('outcome'),verification=result.get('verification_v3')),ensure_ascii=False))
        return int(result.get('outcome',{}).get('status')!='answered')
    return 0

if __name__=='__main__':raise SystemExit(main())
