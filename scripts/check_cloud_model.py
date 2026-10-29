"""默认只查云端型号；--generate显式执行一次小型JSON生成检查。"""
from __future__ import annotations
import argparse,asyncio,json,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env',default='.env')
    parser.add_argument('--generate',action='store_true')
    parser.add_argument('--config-only',action='store_true')
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT/args.env,override=True)
    try:
        from src.agent.providers import resolve_chat_provider
        config=resolve_chat_provider()
        if not config.is_configured:
            print(json.dumps(dict(ok=False,reason='cloud_api_key_required')));return 2
        if args.config_only:
            print(json.dumps(dict(ok=True,config=config.describe(),generation_calls=0)));return 0
        from src.api.readiness import probe_llm
        ok,detail=probe_llm()
        if not ok:
            print(json.dumps(dict(ok=False,detail=detail),ensure_ascii=False));return 2
        result=dict(ok=True,detail=detail,generation_calls=0)
        if args.generate:
            from pydantic import BaseModel,ConfigDict
            from src.agent.v3.model import AsyncModel,Budget
            class Probe(BaseModel):
                model_config=ConfigDict(extra='forbid')
                ok:bool
            async def run():
                budget=Budget(time.time()+60,finalization_reserve=1)
                value=await AsyncModel().structured(Probe,'返回JSON对象，ok填写true。',{},budget)
                return value,budget
            value,budget=asyncio.run(run())
            result.update(ok=value.ok,generation_calls=budget.calls,model_timings=budget.timings)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 2
    except Exception as exc:
        print(json.dumps(dict(ok=False,reason=getattr(exc,'reason',type(exc).__name__)),ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
