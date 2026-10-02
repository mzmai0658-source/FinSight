"""作品说明：采用向量召回、词法召回与规则重排定位叙述证据。指标词组用于召回加权，不作为财务事实口径等价证明；报告期权重随请求计算，不绑定固定公司。"""

import json
import re
from threading import RLock
from typing import Any, Dict, List, Optional

from loguru import logger

from config.rag_config import (
    RAG_RESULT_LIMIT_EXPLANATORY,
    RAG_VECTOR_TOPK_MULTIPLIER,
    build_source_title,
)
from src.agent.domain import CODE_TO_NAME_MAP
from .facts import document_identity, metadata_matches, request_scope, scoped_metadata
from src.agent.providers import assert_collection_embedding, resolve_embedding_function
from src.agent.tool_shared import normalize_reference_path, resolve_chroma_db_path


# 作品说明：指标语义分组：RAG 召回加权用（不绑定任何特定公司/产品）
INDICATOR_KEYWORDS: Dict[str, List[str]] = {
    "收入": ["收入", "营收", "主营业务收入", "营业总收入", "营业收入", "销售额", "销售收入"],
    "利润": ["利润", "净利", "净利润", "利润总额", "归母净利润", "营业利润", "扣非净利润"],
    "毛利率": ["毛利率", "净利率", "毛利", "盈利水平", "利润率"],
    "费用": ["费用", "销售费用", "管理费用", "研发费用", "财务费用", "期间费用"],
    "现金流": ["现金流", "经营活动现金流", "投资活动现金流", "筹资活动现金流", "现金流量", "净现金流"],
    "资产": ["资产", "总资产", "流动资产", "非流动资产", "资产规模"],
    "负债": ["负债", "总负债", "流动负债", "非流动负债", "资产负债率"],
    "股东权益": ["股东权益", "净资产", "每股净资产", "净资产收益率", "roe", "ROE"],
    "业务板块": ["业务板块", "产品线", "品类", "渠道", "终端", "并表", "板块"],
}
LOW_WEIGHT_WORDS: List[str] = [
    "公司治理", "股东回报", "规范运作", "信息披露",
    "投资者关系", "高质量发展", "组织架构", "内控体系",
]
BUSINESS_SEGMENT_WORDS: List[str] = [
    "业务", "板块", "产品", "品类", "渠道", "终端", "并表", "产品线",
]


_EXPLICIT_INTERIM_KEYWORDS = [
    "前三季度", "一季度", "二季度", "三季度", "第一季度", "第二季度", "第三季度",
    "半年报", "半年度", "上半年", "中报",
    "单季", "季报", "分季度", "各季度", "按季度",
]

_ANNUAL_EXPLICIT_KEYWORDS = ["年报", "年度报告", "全年", "FY", "财年"]

_MULTI_YEAR_KEYWORDS = [
    "历年", "多年", "逐年", "每年", "各年",
    "年度趋势", "年度变化", "最近几年", "近几年",
]

_TREND_KEYWORDS = ["趋势", "走势", "变化趋势", "绘图", "可视化", "做图"]


def compute_period_weights(query: str) -> Dict[str, float]:
    """作品说明：根据用户问法动态计算年报 / 季报半年报的权重。
    
    返回 {\"annual\": 0~1, \"interim\": 0~1}
    - annual  越高 → 年度报告来源越优先
    - interim 越高 → 季报/半年报来源越优先
    两者**不互斥**：generic query 两边都可以 0.5。
    """
    q = str(query or "")
    annual = 0.5
    interim = 0.5

    if any(k in q for k in _EXPLICIT_INTERIM_KEYWORDS):
        interim = min(interim + 0.5, 1.0)
        annual = max(annual - 0.3, 0.1)

    if any(k in q for k in _ANNUAL_EXPLICIT_KEYWORDS):
        annual = min(annual + 0.5, 1.0)
        interim = max(interim - 0.3, 0.1)

    if re.search(r"(?:近|过去|最近|最新)\s*[二两三四五六七八九十\d]+\s*年", q):
        annual = min(annual + 0.4, 1.0)
        interim = max(interim - 0.2, 0.15)
    if any(k in q for k in _MULTI_YEAR_KEYWORDS):
        annual = min(annual + 0.3, 1.0)
        interim = max(interim - 0.15, 0.15)

    if any(k in q for k in _TREND_KEYWORDS):
        annual = min(annual + 0.2, 1.0)
        interim = max(interim - 0.1, 0.2)

    if re.search(r"第?[一二三四1-4]季度", q):
        interim = min(interim + 0.4, 1.0)
        annual = max(annual - 0.2, 0.15)

    if any(k in q for k in ["上半年", "半年度", "半年报", "中报"]):
        interim = min(interim + 0.4, 1.0)
        annual = max(annual - 0.2, 0.15)

    if any(k in q for k in ["原因", "归因", "为什么", "驱动", "影响"]):
        if annual > interim:
            annual = min(annual + 0.1, 1.0)
        elif interim > annual:
            interim = min(interim + 0.1, 1.0)

    return {"annual": round(annual, 2), "interim": round(interim, 2)}


class RAGTool:
    """作品说明：研报/年报 RAG 检索工具：向量召回 + 词法召回 + 规则重排。
    
    chroma client / collection / embedding function 在首次使用后缓存，
    避免每次检索都重新打开持久化库和重建嵌入客户端。
    """

    name = "rag_tool"

    def __init__(self, *, rerank_enabled: bool = True):
        self.rerank_enabled = rerank_enabled
        self.chroma_db_path = str(resolve_chroma_db_path())
        self.collection_name = "financial_reports"
        self._init_lock = RLock()
        self._embedding_fn = None
        self._collection = None

    def _get_embedding_function(self):
        """作品说明：向量函数由 providers 统一解析：本地开源 BGE 为主，DashScope 为可选回退。"""
        with self._init_lock:
            if self._embedding_fn is None:
                self._embedding_fn = resolve_embedding_function(purpose="rag_query")
            return self._embedding_fn

    def _get_collection(self):
        with self._init_lock:
            if self._collection is not None:
                return self._collection
            import chromadb
            client = chromadb.PersistentClient(path=self.chroma_db_path)
            ef = self._get_embedding_function()
            try:
                collection = client.get_collection(
                    name=self.collection_name,
                    embedding_function=ef,
                )
            except Exception as e:
                if "Embedding function conflict" not in str(e):
                    raise
                raise RuntimeError(
                    f"知识库 {self.collection_name} 的向量配置与当前不一致：{e}。"
                    "请用当前配置重建知识库（python scripts/rebuild_kb.py）"
                ) from e
            assert_collection_embedding(collection, ef)
            self._collection = collection
            return self._collection

    def _embed_query_text(self, query: str) -> List[float]:
        ef = self._get_embedding_function()
        if hasattr(ef, "embed_query"):
            vector = ef.embed_query(query)
        else:
            vectors = ef([query])
            vector = vectors[0] if vectors else []
        return list(vector or [])

    def _format_rag_results(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        formatted = []
        for rank, item in enumerate(items, start=1):
            doc = item.get("doc")
            meta = item.get("meta") or {}
            paper_path = normalize_reference_path(meta.get("source", "unknown"))
            formatted.append(document_identity({
                "paper_path": paper_path,
                "source_title": str(meta.get("source_title") or build_source_title(paper_path)),
                "source_json": meta.get("source_json"),
                "text": doc,
                "score": int(item.get("score") or 0),
                "rank": rank,
                "paper_image": meta.get("paper_image"),
                "stock_code": meta.get("stock_code"),
                "report_year": meta.get("report_year"),
                "doc_type": meta.get("type"),
                "doc_category": meta.get("doc_category"),
                "report_period": meta.get("report_period"),
                "report_kind": meta.get("report_kind"),
                "section_title": meta.get("section_title"),
                "title_path": meta.get("title_path"),
                "page_start": meta.get("page_start"),
                "page_end": meta.get('page_end'),
                "source_sha256": meta.get('source_sha256'),
                "extraction_method": meta.get('extraction_method'),
                "document_id": meta.get("document_id"),
                "document_version": meta.get("document_version") or meta.get("source_sha256"),
                "chunk_id": meta.get("chunk_id"),
            }))
        return formatted

    def _tokenize_query(self, query: str) -> List[str]:
        base_tokens = [tok.strip() for tok in re.split(r"[\s,，。；;、：:（）()]+", str(query or "")) if tok.strip()]
        extra_tokens = re.findall(r"[\u4e00-\u9fff]{2,}|\d{4}年|\d{6}|[A-Za-z]{2,}", str(query or ""))
        ordered: List[str] = []
        for token in base_tokens + extra_tokens:
            if token not in ordered:
                ordered.append(token)
        return ordered

    def _is_explanatory_query(self, query: str) -> bool:
        return any(k in str(query or "") for k in ["原因", "归因", "为什么", "分析", "驱动", "影响", "逻辑"])

    def _extract_target_indicator(self, query: str) -> Optional[str]:
        best_indicator = None
        best_score = -1
        for indicator, keywords in INDICATOR_KEYWORDS.items():
            score = sum(2 if len(keyword) >= 4 else 1 for keyword in keywords if keyword in str(query or ""))
            if score > best_score:
                best_score = score
                best_indicator = indicator
        return best_indicator if best_score > 0 else None

    def _is_research_query_text(self, query: str) -> bool:
        research_keywords = [
            "研报", "行业研究", "行业风向", "政策", "医保", "集采", "审批",
            "竞争格局", "市场份额", "行业趋势", "赛道",
        ]
        return any(k in str(query or "") for k in research_keywords)

    def _preferred_doc_types(self, query: str) -> List[str]:
        if self._is_research_query_text(query) and not self._is_explanatory_query(query):
            return [
                "research_report_policy",
                "research_report_industry",
                "research_report_equity",
                "research_report_conclusion",
                "xlsx_summary",
            ]
        if self._is_explanatory_query(query):
            return [
                "financial_report_reasoning",
                "financial_report_operation_note",
                "financial_report_mda",
                "research_report_equity",
                "research_report_industry",
                "research_report_policy",
                "research_report_conclusion",
            ]
        return [
            "research_report_equity",
            "research_report_industry",
            "financial_report_mda",
            "financial_report_operation_note",
            "xlsx_summary",
        ]

    def _preferred_doc_category(self, query: str) -> Optional[str]:
        if self._is_research_query_text(query) and not self._is_explanatory_query(query):
            return "research"
        return None

    def _compose_where(self, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized = [{key: value} for key, value in filters.items() if value not in (None, "")]
        if not normalized:
            return None
        if len(normalized) == 1:
            return normalized[0]
        return {"$and": normalized}

    def _score_rag_candidate(
        self,
        query: str,
        doc: str,
        meta: Dict[str, Any],
        tokens: List[str],
        lexical_score: int = 0,
        vector_rank: Optional[int] = None,
    ) -> int:
        if not self.rerank_enabled:
            return int(lexical_score or 0) + (max(1, 12 - int(vector_rank)) if vector_rank is not None else 0)
        meta = meta or {}
        text = str(doc or "")
        source = str(meta.get("source") or "")
        doc_type = str(meta.get("type") or "")
        doc_category = str(meta.get("doc_category") or "")
        section_title = str(meta.get("section_title") or "")
        title_path = str(meta.get("title_path") or "")
        report_kind = str(meta.get("report_kind") or "")
        score = int(lexical_score or 0)
        target_indicator = self._extract_target_indicator(query)
        indicator_terms = INDICATOR_KEYWORDS.get(str(target_indicator or ""), [])

        if vector_rank is not None:
            score += max(1, 12 - int(vector_rank))

        _is_research_q = self._is_research_query_text(query)

        if _is_research_q and not self._is_explanatory_query(query):
            if doc_type == "research_report_policy":
                score += 18
            elif doc_type == "research_report_industry" or "行业研报" in source:
                score += 15
            elif doc_type == "research_report_equity" or "个股研报" in source:
                score += 12
            elif doc_type == "research_report_conclusion":
                score += 8
            elif "financial_report_mda" in doc_type:
                score += 3
            elif "financial_report_reasoning" in doc_type:
                score += 2
            elif "financial_report_operation_note" in doc_type:
                score += 1
        else:
            if "financial_report_reasoning" in doc_type:
                score += 8
            elif "financial_report_operation_note" in doc_type:
                score += 6
            elif "financial_report_mda" in doc_type:
                score += 7
            elif doc_type == "research_report_equity" or "个股研报" in source:
                score += 3
            elif doc_type == "research_report_industry" or "行业研报" in source:
                score += 1

        if self._is_explanatory_query(query):
            if any(k in text for k in ["变动原因", "主要原因", "受益于", "带动", "推动", "由于", "得益于", "并表", "协同"]):
                score += 6
            if any(k in text for k in ["经营情况", "经营成果", "业务回顾", "主营业务情况", "报告期内经营情况"]):
                score += 4
            if indicator_terms and any(k in text for k in indicator_terms):
                score += 8
            if any(k in text for k in BUSINESS_SEGMENT_WORDS):
                score += 3
            _pw = compute_period_weights(query)
            _is_annual_src = report_kind == "annual" or any(k in source for k in ["年度报告", "年报"])
            _is_interim_src = report_kind == "interim" or any(k in source for k in ["一季度报告", "半年度报告", "三季度报告", "季度报告"])
            if _is_annual_src:
                score += int(14 * _pw["annual"] - 2 * _pw["interim"])
            if _is_interim_src:
                score += int(14 * _pw["interim"] - 2 * _pw["annual"])
            if any(k in text for k in ["风险提示", "维持“买入”评级", "现价对应PE", "分析师"]):
                score -= 4
        else:
            if any(k in text for k in ["结论", "摘要", "图表", "核心观点"]):
                score += 1
            _pw2 = compute_period_weights(query)
            _is_ann2 = report_kind == "annual" or any(k in source for k in ["年度报告", "年报"])
            _is_int2 = report_kind == "interim" or any(k in source for k in ["一季度报告", "半年度报告", "三季度报告", "季度报告"])
            if _is_ann2:
                score += int(10 * _pw2["annual"])
            if _is_int2:
                score += int(10 * _pw2["interim"])

        if any(k in text for k in LOW_WEIGHT_WORDS):
            score -= 8

        _AUDIT_CHUNK_NOISE = ["关键审计事项", "审计报告", "审计意见", "会计师事务所",
                              "鉴证", "审计程序", "持续经营能力",
                              "商誉减值准备", "可回收金额", "资产组"]
        if any(k in text for k in _AUDIT_CHUNK_NOISE):
            score -= 12

        _MDA_NARRATIVE_MARKERS = ["报告期内经营情况", "经营情况讨论与分析",
                                  "管理层讨论与分析", "主营业务分析",
                                  "业务回顾", "经营成果"]
        if any(k in text for k in _MDA_NARRATIVE_MARKERS):
            score += 6

        if doc_category == "research":
            score += 2
        if section_title and any(token in section_title for token in tokens if len(token) >= 2):
            score += 3
        if title_path and any(token in title_path for token in tokens if len(token) >= 2):
            score += 2

        for token in tokens:
            if token and token in text:
                score += 2 if len(token) >= 4 else 1
            if token and token in source:
                score += 2 if len(token) >= 4 else 1
            if token and token in doc_type:
                score += 1

        return score

    def _lexical_scored_results(
        self,
        collection,
        query: str,
        top_k: int,
        stock_code: Optional[str],
        report_year: Optional[int],
    ) -> List[Dict[str, Any]]:
        where: Dict[str, Any] = {}
        if stock_code:
            where["stock_code"] = str(stock_code)
        if report_year:
            where["report_year"] = int(report_year)
        preferred_category = self._preferred_doc_category(query)
        if not where and preferred_category:
            where["doc_category"] = preferred_category

        where_clause = self._compose_where(where)
        payload = self._safe_collection_get(
            collection,
            where_clause=where_clause,
            include=["documents", "metadatas"],
        )
        documents = payload.get("documents") or []
        metadatas = payload.get("metadatas") or []
        tokens = self._tokenize_query(query)
        result_limit = max(
            int(top_k or 3),
            RAG_RESULT_LIMIT_EXPLANATORY if self._is_explanatory_query(query) else int(top_k or 3),
        )
        scored: List[Dict[str, Any]] = []

        company_name = CODE_TO_NAME_MAP.get(str(stock_code or ""), "") if stock_code else ""

        from .citations import requested_section, section_matches
        section = requested_section(query)
        for doc, meta in zip(documents, metadatas):
            meta = meta or {}
            if section and not section_matches(section, str(doc or ''), meta):
                continue
            meta_sc = str(meta.get("stock_code") or "")
            if stock_code and meta_sc != str(stock_code):
                if not (company_name and company_name in str(meta.get("source") or "")):
                    continue
            if report_year and int(meta.get("report_year") or 0) not in {0, int(report_year)}:
                continue

            text = str(doc or "")
            haystack = (text, str(meta.get("source") or ""), str(meta.get("type") or ""))
            lexical_score = 0
            for token in tokens:
                if any(token in field for field in haystack):
                    lexical_score += 3 if len(token) >= 4 else 1
            if section:
                lexical_score += 20
            if lexical_score <= 0:
                continue

            scored.append({
                "doc": doc,
                "meta": meta,
                "score": self._score_rag_candidate(query, text, meta, tokens, lexical_score=lexical_score),
            })

        scored.sort(key=lambda item: (int(item["score"]), len(str(item["doc"]))), reverse=True)
        return scored[:result_limit]

    def _safe_collection_get(
        self,
        collection,
        where_clause: Optional[Dict[str, Any]],
        include: List[str],
        batch_size: int = 5000,
    ) -> Dict[str, List[Any]]:
        # 作品说明：Chroma 会先取出全部命中 id，再拼成 SQL IN 参数；大集合的无界 get
        # 不仅必然撞上 SQLite 变量上限，还会白白等待一次失败查询。已知是大集合时
        # 直接分页，既保持完整词法召回语义，也显著减少数据库往返次数。
        try:
            should_page = int(collection.count()) > int(batch_size)
        except Exception:
            should_page = False

        if not should_page:
            try:
                kwargs: Dict[str, Any] = {"include": include}
                if where_clause:
                    kwargs["where"] = where_clause
                return collection.get(**kwargs)
            except Exception as exc:
                if "too many SQL variables" not in str(exc):
                    raise

        documents: List[Any] = []
        metadatas: List[Any] = []
        offset = 0
        while True:
            kwargs = {
                "include": include,
                "limit": int(batch_size),
                "offset": int(offset),
            }
            if where_clause:
                kwargs["where"] = where_clause
            payload = collection.get(**kwargs)
            batch_docs = payload.get("documents") or []
            batch_meta = payload.get("metadatas") or []
            if not batch_docs:
                break
            documents.extend(batch_docs)
            metadatas.extend(batch_meta)
            if len(batch_docs) < batch_size:
                break
            offset += batch_size

        return {"documents": documents, "metadatas": metadatas}

    def run(
        self,
        query: str,
        top_k: int = 3,
        stock_code: Optional[str] = None,
        report_year: Optional[int] = None,
        report_period: Optional[str] = None,
        validated_scope: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            if validated_scope is not None:
                # 作品说明：检索排序使用 Agent 已解析的身份，叙述检索不能重选公司或期间。
                scope = dict(validated_scope)
                if not scope.get('stock_codes') or not scope.get('report_years') or scope.get('report_period') not in {'FY','Q1','HY','Q3'}:
                    return {'status':'error','results':[],'message':'结构化检索缺少有效主体或报告期'}
            else:
                scope = request_scope(query)
            if stock_code:
                if scope["stock_codes"] and str(stock_code) not in scope["stock_codes"]:
                    return {"status": "empty", "results": [], "message": "公司过滤条件与检索问题不一致"}
                scope["stock_codes"] = [str(stock_code)]
            if report_year:
                if scope["report_years"] and int(report_year) not in scope["report_years"]:
                    return {"status": "empty", "results": [], "message": "年份过滤条件与检索问题不一致"}
                scope["report_years"] = [int(report_year)]
            if report_period:
                if scope["report_period"] and scope["report_period"] != report_period:
                    return {"status": "empty", "results": [], "message": "报告期过滤条件与检索问题不一致"}
                scope["report_period"] = report_period
            stock_code = stock_code or (scope["stock_codes"][0] if len(scope["stock_codes"]) == 1 else None)
            report_year = report_year or (scope["report_years"][0] if len(scope["report_years"]) == 1 else None)
            top_k = max(1, min(int(top_k), 20))
            collection = self._get_collection()
            tokens = self._tokenize_query(query)
            result_limit = max(
                int(top_k or 3),
                RAG_RESULT_LIMIT_EXPLANATORY if self._is_explanatory_query(query) else int(top_k or 3),
            )
            lexical_candidates = self._lexical_scored_results(collection, query, result_limit, stock_code, report_year)

            base_where: Dict[str, Any] = {}
            if stock_code:
                base_where["stock_code"] = str(stock_code)
            if report_year:
                base_where["report_year"] = int(report_year)
            preferred_doc_types = self._preferred_doc_types(query)
            preferred_category = self._preferred_doc_category(query)

            query_vector: Optional[List[float]] = None
            try:
                query_vector = self._embed_query_text(query)
            except Exception as e:
                logger.warning(f"RAG 向量嵌入失败，回退 lexical-only: {e}")
            n_vec = max(result_limit, int(top_k or 3) * RAG_VECTOR_TOPK_MULTIPLIER)

            vector_candidates: List[Dict[str, Any]] = []
            seen_vec_keys: set = set()

            def _collect_vector(qkw: Dict[str, Any]):
                try:
                    results = collection.query(**qkw)
                    docs = results.get("documents", [[]])[0]
                    metas = results.get("metadatas", [[]])[0]
                    for idx, (doc, meta) in enumerate(zip(docs, metas), start=1):
                        meta = scoped_metadata(meta or {})
                        if not metadata_matches(meta, scope):
                            continue
                        vk = f"{(meta or {}).get('source','')}::{(doc or '')[:180]}"
                        if vk in seen_vec_keys:
                            continue
                        seen_vec_keys.add(vk)
                        vector_candidates.append({
                            "doc": doc,
                            "meta": meta or {},
                            "score": self._score_rag_candidate(query, str(doc or ""), meta or {}, tokens, vector_rank=idx),
                        })
                except Exception as e:
                    logger.warning(f"RAG 向量检索失败: {e}")

            seen_where_keys = set()

            def _try_where(where_obj: Optional[Dict[str, Any]]):
                if not query_vector:
                    return
                qkw: Dict[str, Any] = {"query_embeddings": [query_vector], "n_results": n_vec}
                if where_obj:
                    where_key = json.dumps(where_obj, sort_keys=True, ensure_ascii=False)
                    if where_key in seen_where_keys:
                        return
                    seen_where_keys.add(where_key)
                    qkw["where"] = self._compose_where(where_obj)
                _collect_vector(qkw)

            for doc_type in preferred_doc_types[:6]:
                where_obj = dict(base_where)
                where_obj["type"] = doc_type
                _try_where(where_obj)

            if preferred_category:
                where_obj = dict(base_where)
                where_obj["doc_category"] = preferred_category
                _try_where(where_obj)

            if base_where:
                _try_where(dict(base_where))

            # 作品说明：兜底：不带 where 再查一轮（覆盖 stock_code / report_year 元数据缺失的 chunk）
            if query_vector:
                _collect_vector({"query_embeddings": [query_vector], "n_results": n_vec})

            merged: Dict[str, Dict[str, Any]] = {}
            for item in lexical_candidates + vector_candidates:
                meta = scoped_metadata(item.get("meta") or {})
                if not metadata_matches(meta, scope):
                    continue
                doc = str(item.get("doc") or "")
                key = f"{meta.get('source', '')}::{doc[:180]}"
                current = merged.get(key)
                if current is None or int(item.get("score") or 0) > int(current.get("score") or 0):
                    merged[key] = {
                        "doc": doc,
                        "meta": meta,
                        "score": int(item.get("score") or 0),
                    }

            ordered = sorted(
                merged.values(),
                key=lambda item: (int(item["score"]), len(str(item["doc"]))),
                reverse=True,
            )
            from .citations import requested_section, section_matches
            section = requested_section(query)
            if section:
                ordered = [item for item in ordered if section_matches(section, str(item['doc'] or ''), item['meta'])]
                ordered.sort(key=lambda item: (int(item['meta'].get('page_start') or 10**9),
                                               -int(section in str(item['doc'])), -len(str(item['doc']))))
                # 作品说明：请求摘录时只返回相关来源，不混入无关参考卡片。
                ordered = ordered[:1]
            results = self._format_rag_results(ordered[:result_limit])
            if results:
                return {"status": "success", "results": results, "scope": scope}
            return {"status": "empty", "message": "未找到主体及报告期符合要求的原文证据", "results": [], "scope": scope}

        except Exception as e:
            logger.error(f"RAG检索失败: {e}")
            return {"status": "error", "message": str(e), "results": []}
