"""Java迁移完成后补齐财报正文表的只读权限，不授权用户或会话业务表。"""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.etl.real_delivery import create_reader
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env',default='.env');a=p.parse_args()
    load_dotenv(ROOT/a.env,override=True);result=create_reader()
    if result['deferred_until_java_migration']:raise RuntimeError('财报正文表尚未迁移；先启动Java，再补齐只读权限')
    print(json.dumps(dict(readonly_financial_tables=len(result['granted']),user_and_chat_tables_granted=False)))
