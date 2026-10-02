from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class StatusItem(BaseModel):
    ok: bool
    detail: str


class HealthResponse(BaseModel):
    service: StatusItem
    database: StatusItem
    knowledge_base: StatusItem
    llm: StatusItem
    examples: StatusItem
    ocr: Optional[StatusItem] = None


class InternalHistoryItem(BaseModel):
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class InternalChatRequest(BaseModel):
    """作品说明：内部无状态对话请求：会话状态由 Java 主后端持有，这里只接收本轮所需上下文。"""

    question: str = Field(..., min_length=1, max_length=2000)
    history: List[InternalHistoryItem] = Field(default_factory=list)
    session_uid: Optional[str] = Field(default=None, description="Java 侧会话 UUID，用于图表文件命名")
    chart_prefix: Optional[str] = None
    chart_index: Optional[int] = Field(default=None, ge=1)
    version: int = Field(default=3, ge=3, le=3)
    task_id: Optional[str] = Field(default=None, max_length=64)
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    deadline: Optional[int] = Field(default=None, description='Unix epoch milliseconds')

    @field_validator('question', mode='before')
    @classmethod
    def normalize_question(cls, value):
        return value.strip() if isinstance(value, str) else value


class InternalTitleRequest(BaseModel):
    """作品说明：会话标题概括请求（Java MQ 消费者调用）。"""

    question: str = Field(..., min_length=1)
    answer: str = ""


class InternalTitleResponse(BaseModel):
    title: str


class InternalEtlRequest(BaseModel):
    """作品说明：单文件 ETL 管线请求（Java etl_task 消费者调用）。"""

    file_path: str = Field(..., min_length=1)
    file_type: str = Field(default="financial", description="financial=财报 research=研报")
    ingest_rag: bool = Field(default=False, description="是否做 RAG 入库（依赖向量服务）")


class InternalAdvisorRequest(BaseModel):
    """作品说明：AI 诊股报告生成请求（Java advisor 消费者调用）。"""

    stock_code: str = Field(..., min_length=1)
    stock_name: str = ""
    risk_profile: str = Field(default="balanced", description="conservative/balanced/aggressive")
    score: dict = Field(default_factory=dict, description="Java 规则评分结果（总分/评级/分维度）")
    metrics: dict = Field(default_factory=dict, description="近年关键财务指标")


class InternalAdvisorResponse(BaseModel):
    report_md: str
