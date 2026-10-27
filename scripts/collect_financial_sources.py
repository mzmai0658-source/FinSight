"""Discover/download the approved new 120-report cohort without OCR or credentials.

Examples (from the repository root)::

    python scripts/collect_financial_sources.py --stage all
    python scripts/collect_financial_sources.py --stage discover --refresh-metadata
    python scripts/collect_financial_sources.py --stage download --max-downloads 12
    python scripts/collect_financial_sources.py --stage status

Exit code 2 means the requested acquisition stage is incomplete. Its manifest
retains missing/error slots and completed originals so the same command resumes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The existing src.etl package eagerly imports database/OCR clients. Loading this
# independent module by path keeps a public-source command free of those effects.
_SPEC = importlib.util.spec_from_file_location("public_report_acquisition", ROOT / "src/etl/report_acquisition.py")
acquisition = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acquisition)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("discover", "download", "all", "status"), default="all")
    parser.add_argument("--cohort", type=Path, default=ROOT / "config/cloud_upload120_cohort.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/runtime/cloud_upload120_sources.json")
    parser.add_argument("--destination", type=Path, default=ROOT / "data_root/financial_reports/new120")
    parser.add_argument("--max-downloads", type=int)
    parser.add_argument("--metadata-probes", type=int, default=2)
    parser.add_argument("--refresh-metadata", action="store_true")
    args = parser.parse_args(argv)
    cohort = acquisition.load_cohort(args.cohort)
    if args.stage in ("discover", "all"):
        manifest = acquisition.discover_cninfo_sources(cohort, args.manifest, max_metadata_probes=args.metadata_probes, refresh=args.refresh_metadata)
    if args.stage in ("download", "all"):
        manifest = acquisition.acquire_reports(cohort, args.destination, args.manifest, limit=args.max_downloads)
    if args.stage == "status":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest.exists() else acquisition.initialize_manifest(cohort)
    print(json.dumps({"cohort_id": cohort["cohort_id"], "stage": args.stage, "manifest": str(args.manifest.resolve()), "counts": manifest["counts"], "complete": manifest["complete"], "network_blocked": manifest.get("metadata", {}).get("network_blocked", False), "missing_reports": [row["report_id"] for row in manifest["reports"] if not row.get("source_url")], "failed_reports": [{"report_id": row["report_id"], "error": row.get("error")} for row in manifest["reports"] if row.get("status") == "error"]}, ensure_ascii=False, indent=2))
    if args.stage == "status":
        return 0
    if args.stage == "discover":
        return 0 if not manifest["counts"]["missing"] and not manifest.get("metadata", {}).get("network_blocked") else 2
    return 0 if manifest["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
