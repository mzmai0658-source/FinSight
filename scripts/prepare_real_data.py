"""校验真实资料附件，从原件重建或导入已核验事实，生成新索引后原子发布。"""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.etl.real_delivery import initialize
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env',type=Path,default=ROOT/'.env');p.add_argument('--packages',type=Path)
    p.add_argument('--mode',choices=('quick','rebuild'),default='rebuild')
    p.add_argument('--output',default='data/runtime/real-rebuild');p.add_argument('--create-database',action='store_true');p.add_argument('--create-reader',action='store_true')
    a=p.parse_args();print(json.dumps(initialize(a.env.resolve(),a.mode,a.packages,a.output,a.create_database,a.create_reader),ensure_ascii=False))
