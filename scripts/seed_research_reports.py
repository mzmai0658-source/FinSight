"""作品说明：研报元数据入库：metadata xlsx → research_report 表。

用法（项目根目录执行，需先启动 Java 让 Flyway 建表）：
.venv\\Scripts\\python.exe scripts\\seed_research_reports.py
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine, text

from config.db_config import get_db_config
from src.etl.research_metadata import load_research_metadata, normalize_title
from src.utils.data_paths import find_research_reports_root


def _collect_local_pdf_titles() -> set:
    root = find_research_reports_root()
    titles = set()
    if root:
        for sub in ("个股研报", "行业研报"):
            base = root / sub
            if base.is_dir():
                for pdf in base.rglob("*.pdf"):
                    titles.add(normalize_title(pdf.stem))
    return titles


UPSERT_SQL = text("""
    INSERT INTO research_report
        (title, report_type, stock_code, stock_name, org_name, org_sname, publish_date,
         industry_name, rating, last_rating, researcher,
         predict_this_year_eps, predict_this_year_pe, aim_price, dataset_tag, pdf_present)
    VALUES
        (:title, :report_type, :stock_code, :stock_name, :org_name, :org_sname, :publish_date,
         :industry_name, :rating, :last_rating, :researcher,
         :predict_eps, :predict_pe, :aim_price, :dataset_tag, :pdf_present)
    ON DUPLICATE KEY UPDATE
        stock_code = VALUES(stock_code), rating = VALUES(rating),
        industry_name = VALUES(industry_name), pdf_present = VALUES(pdf_present),
        dataset_tag = VALUES(dataset_tag)
""")


def main():
    index = load_research_metadata()
    if not index:
        print("未找到研报元数据，请检查 data_root/research_reports/metadata")
        return

    local_titles = _collect_local_pdf_titles()
    print(f"元数据 {len(index)} 条，本地研报 PDF {len(local_titles)} 份")

    engine = create_engine(get_db_config().connection_string)
    inserted = 0
    with engine.begin() as conn:
        for key, meta in index.items():
            extra = meta.extra or {}
            conn.execute(UPSERT_SQL, {
                "title": meta.title[:300],
                "report_type": meta.report_type,
                "stock_code": meta.stock_code,
                "stock_name": meta.stock_name[:50],
                "org_name": meta.org_name[:120],
                "org_sname": meta.org_sname[:60],
                "publish_date": meta.publish_date or None,
                "industry_name": meta.industry_name[:80],
                "rating": meta.rating[:30],
                "last_rating": extra.get("last_rating", "")[:30],
                "researcher": meta.researcher[:120],
                "predict_eps": extra.get("predict_this_year_eps", "")[:20],
                "predict_pe": extra.get("predict_this_year_pe", "")[:20],
                "aim_price": extra.get("aim_price", "")[:20],
                "dataset_tag": meta.dataset_tag[:20],
                "pdf_present": 1 if key in local_titles else 0,
            })
            inserted += 1

    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM research_report")).scalar()
        with_pdf = conn.execute(text("SELECT COUNT(*) FROM research_report WHERE pdf_present = 1")).scalar()
        stock = conn.execute(text("SELECT COUNT(*) FROM research_report WHERE report_type = 'stock'")).scalar()
    print(f"处理 {inserted} 条 → 库内共 {total} 条（个股 {stock}，有 PDF 原文 {with_pdf}）")


if __name__ == "__main__":
    main()
