"""作品说明：当前 v3 命令入口。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.v3_smoke_chat import main
if __name__=="__main__":raise SystemExit(main())
