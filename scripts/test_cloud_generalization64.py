"""PowerShell 5.1 compatible entry for the existing isolated Java/SSE evaluation."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    from eval.cloud_generalization64 import main
    raise SystemExit(main())
