"""作品说明：单文件 ETL 管线：管理端上传与批量脚本共用的标准处理链路。

步骤：OCR 缓存解析 → 结构化抽取 → 入库 → 公司级同比重算 → company 主数据回写 → RAG 入库（可选）。
每一步的状态都会记录到 PipelineReport，供 etl_task 状态机展示。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.etl.derived import recompute_company_derived
from src.etl.ocr_client import OcrNotConfiguredError, ensure_ocr_json


@dataclass
class StepResult:
    name: str
    status: str  # 作品说明：结果状态区分成功、跳过与失败。
    detail: str = ""
    elapsed_ms: int = 0


@dataclass
class PipelineReport:
    status: str = "pending"  # 作品说明：批次状态区分成功、部分完成与失败。
    file: str = ""
    stock_code: str = ""
    stock_abbr: str = ""
    report_year: Optional[int] = None
    report_period: str = ""
    message: str = ""
    steps: List[StepResult] = field(default_factory=list)

    def add_step(self, name: str, status: str, detail: str = "", elapsed_ms: int = 0) -> None:
        self.steps.append(StepResult(name, status, detail, elapsed_ms))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "file": self.file,
            "stock_code": self.stock_code,
            "stock_abbr": self.stock_abbr,
            "report_year": self.report_year,
            "report_period": self.report_period,
            "message": self.message,
            "retryable_steps": [step.name for step in self.steps if step.status == "failed"],
            "steps": [
                {"name": s.name, "status": s.status, "detail": s.detail, "elapsed_ms": s.elapsed_ms}
                for s in self.steps
            ],
        }


class _Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args):
        pass

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.start) * 1000)


def _update_company_master(stock_code: str, stock_abbr: str) -> str:
    """作品说明：财务数据入库后回写 company 主数据：状态置 imported，年份范围取库内极值。"""
    from sqlalchemy import create_engine, text

    from config.db_config import get_db_config

    engine = create_engine(get_db_config().connection_string, pool_pre_ping=True)
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT MIN(report_year), MAX(report_year) "
            "FROM core_performance_indicators_sheet WHERE stock_code = :code"
        ), {"code": stock_code}).fetchone()
        first_year, last_year = (row[0], row[1]) if row else (None, None)
        updated = conn.execute(text(
            "UPDATE company SET data_status = 'imported', first_year = :fy, last_year = :ly "
            "WHERE stock_code = :code"
        ), {"fy": first_year, "ly": last_year, "code": stock_code}).rowcount
        if not updated:
            conn.execute(text(
                "INSERT INTO company (stock_code, abbr, data_status, first_year, last_year) "
                "VALUES (:code, :abbr, 'imported', :fy, :ly)"
            ), {"code": stock_code, "abbr": stock_abbr or stock_code, "fy": first_year, "ly": last_year})
    return f"first_year={first_year} last_year={last_year}"


def run_single_file(
    pdf_path: str | Path,
    save_to_db: bool = True,
    ingest_rag: bool = True,
    llm_client=None,
) -> PipelineReport:
    """作品说明：处理单份财报 PDF 的完整管线。
    
    OCR/抽取/入库任一步失败立即终止；同比重算、company 回写、RAG 入库失败返回 partial 和可重试步骤。
    """
    pdf_path = Path(pdf_path)
    report = PipelineReport(file=str(pdf_path))

    # 作品说明：1. OCR 缓存（本地命中或远端服务，远端未配置则失败）
    with _Timer() as t:
        try:
            json_path = ensure_ocr_json(pdf_path)
            report.add_step("ocr", "ok", json_path.name, t.elapsed_ms)
        except OcrNotConfiguredError as e:
            report.add_step("ocr", "failed", str(e))
            report.status, report.message = "failed", str(e)
            return report
        except Exception as e:
            report.add_step("ocr", "failed", str(e))
            report.status, report.message = "failed", f"OCR 失败: {e}"
            return report

    # 作品说明：2. 结构化抽取（复用 ETLWorker，行为与批量链路一致）
    if llm_client is None:
        from src.agent.llm_client import LLMClient

        llm_client = LLMClient()
    from src.etl.etl_worker import ETLWorker

    worker = ETLWorker(llm_client)
    with _Timer() as t:
        try:
            # 作品说明：直接喂 OCR JSON：上传副本目录下无缓存时（工作区命中）worker 也能读到同一份内容
            result = worker.run(str(json_path), save_to_db=False, source_pdf_path=str(pdf_path))
        except Exception as e:
            report.add_step("extract", "failed", str(e))
            report.status, report.message = "failed", f"抽取失败: {e}"
            return report

    data = result.get("data") or {}
    report.stock_code = str(data.get("stock_code") or "")
    report.stock_abbr = str(data.get("stock_abbr") or "")
    report.report_year = data.get("report_year")
    report.report_period = str(data.get("report_period") or "")

    if result.get("status") != "success":
        report.add_step("extract", "failed", str(result.get("message") or ""), t.elapsed_ms)
        report.status, report.message = "failed", str(result.get("message") or "抽取失败")
        return report
    report.add_step(
        "extract", "ok",
        f"{report.stock_code} {report.report_year}{report.report_period}", t.elapsed_ms,
    )

    if not save_to_db:
        report.add_step("save_db", "skipped", "save_to_db=False")
        report.status = "success"
        return report

    # 作品说明：3. 入库（公司级回填 + 写四张表）
    with _Timer() as t:
        try:
            worker.backfill_cross_file_growths([result])
            saved = worker.save_records_to_db([result])
            if not saved or saved.get('committed',0) < 1:
                raise RuntimeError('Database write did not confirm any committed records')
            report.add_step("save_db", "ok", f"{saved['committed']} 条记录完整提交", t.elapsed_ms)
        except Exception as e:
            report.add_step("save_db", "failed", str(e))
            report.status, report.message = "failed", f"入库失败: {e}"
            return report

    # 作品说明：4. 公司级同比重算（新报告期落库后相邻年份的 yoy 才可补全）
    with _Timer() as t:
        try:
            updated = recompute_company_derived(report.stock_code)
            report.add_step("derived", "ok", f"回填 {sum(updated.values())} 行", t.elapsed_ms)
        except Exception as e:
            logger.warning(f"[pipeline] 同比重算失败: {e}")
            report.add_step("derived", "failed", str(e))

    # 作品说明：5. company 主数据回写（市场模块据此展示）
    with _Timer() as t:
        try:
            detail = _update_company_master(report.stock_code, report.stock_abbr)
            report.add_step("company", "ok", detail, t.elapsed_ms)
        except Exception as e:
            logger.warning(f"[pipeline] company 回写失败: {e}")
            report.add_step("company", "failed", str(e))

    # 作品说明：6. RAG 入库（依赖向量服务，可关闭）
    if ingest_rag:
        with _Timer() as t:
            try:
                from src.etl.rag_builder import ingest_financial_report_pdf

                inserted = ingest_financial_report_pdf(pdf_path, source_json_path=json_path)
                if inserted == 0:
                    raise ValueError("未提取到可发布的原文片段")
                report.add_step("rag", "ok", "未变化，增量跳过" if inserted < 0 else f"{inserted} 个片段已入库", t.elapsed_ms)
            except Exception as e:
                logger.warning(f"[pipeline] RAG 入库失败: {e}")
                report.add_step("rag", "failed", str(e))
    else:
        report.add_step("rag", "skipped", "ingest_rag=False")

    failed = [step.name for step in report.steps if step.status == "failed"]
    report.status = "partial" if failed else "success"
    report.message = ("财务数据已保存，以下步骤失败，可重试：" + ", ".join(failed)) if failed else f"{report.stock_code} {report.report_year}{report.report_period} 处理完成"
    return report


def run_research_file(pdf_path: str | Path, ingest_rag: bool = True) -> PipelineReport:
    """作品说明：处理单份研报 PDF：text-first 优先，仅做 RAG 入库（研报无结构化数值）。"""
    pdf_path = Path(pdf_path)
    report = PipelineReport(file=str(pdf_path))

    if not ingest_rag:
        report.add_step("rag", "skipped", "ingest_rag=False")
        report.status = "success"
        return report

    with _Timer() as t:
        try:
            from src.etl.rag_builder import ingest_research_pdf

            inserted = ingest_research_pdf(pdf_path)
            if inserted == 0:
                report.add_step("rag", "failed", "无法提取文本", t.elapsed_ms)
                report.status, report.message = "failed", "研报无可用文本（无 OCR 缓存且文字层不可读）"
                return report
            detail = "未变化，增量跳过" if inserted < 0 else f"{inserted} 个片段"
            report.add_step("rag", "ok", detail, t.elapsed_ms)
        except Exception as e:
            report.add_step("rag", "failed", str(e))
            report.status, report.message = "failed", f"研报入库失败: {e}"
            return report

    report.status = "success"
    report.message = "研报处理完成"
    return report
