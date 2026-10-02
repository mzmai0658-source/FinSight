"""作品说明：通过本机实际服务冒烟检查当前 v3 生产接口。"""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.v3_native_client import NativeClient

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('questions',nargs='*');parser.add_argument('--base',default='http://127.0.0.1:18080')
    args=parser.parse_args();client=NativeClient(args.base)
    failed=False
    for question in args.questions or ['同仁堂2024年母公司营业收入是多少？','万邦德2024年归母净利润是多少？']:
        run=client.turn(question);result=run['task'].get('result') or {}
        print(json.dumps(dict(question=question,status=run['task']['status'],saved=run['task']['saved'],outcome=result.get('outcome'),
            verification=result.get('verification_v3'),answer=result.get('answer'),seconds=run['seconds']),ensure_ascii=False))
        failed|=run['task']['status']=='failed'
    return int(failed)

if __name__=='__main__':raise SystemExit(main())
