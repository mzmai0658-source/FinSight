from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime

@dataclass
class AgentState:
    """作品说明：数据导入多阶段共享状态。"""
    # 作品说明：处理上下文。
    user_query: str = ""
    current_step: str = "idle"
    history: List[Dict[str, str]] = field(default_factory=list)

    # 作品说明：数据上下文。
    files_to_process: List[str] = field(default_factory=list)
    extracted_data: List[Dict[str, Any]] = field(default_factory=list)

    # 作品说明：当前文件身份元数据。
    current_file_path: Optional[str] = None
    current_stock_code: Optional[str] = None
    current_stock_abbr: Optional[str] = None
    current_report_year: Optional[int] = None
    current_report_period: Optional[str] = None

    # 作品说明：输出结果。
    final_answer: Optional[str] = None
    sql_query: Optional[str] = None
    sql_result: Optional[List[Dict[str, Any]]] = None

    def update_metadata(self, code, abbr, year, period):
        self.current_stock_code = code
        self.current_stock_abbr = abbr
        self.current_report_year = year
        self.current_report_period = period

    def clear_metadata(self):
        self.current_stock_code = None
        self.current_stock_abbr = None
        self.current_report_year = None
        self.current_report_period = None
