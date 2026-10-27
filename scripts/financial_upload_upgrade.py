"""无需pwsh，使用项目Python运行隔离财报升级与验收。"""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

if __name__=='__main__':
    from src.etl.submission_upgrade import main
    raise SystemExit(main())
