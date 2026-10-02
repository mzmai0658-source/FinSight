"""作品说明：图表工具：生成 PNG 供下载，同时输出前端可直接渲染的 ECharts option。

图表数据必须来自本轮查询结果，不允许工具自行补数；`chart_data` 中回传的
x_data / y_data 是后续一致性校验的依据。
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from src.agent.tool_shared import ROOT_DIR


class ChartTool:
    """作品说明：图表生成工具，支持折线图/柱状图/饼图。"""

    name = "chart_tool"

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = output_dir or str(ROOT_DIR / "data" / "runtime" / "results")
        os.makedirs(self.output_dir, exist_ok=True)

    def run(
        self,
        chart_type: str,
        title: str,
        x_data: List[str],
        y_data: List[float],
        x_label: str = "",
        y_label: str = "",
        filename: str = "",
        series_name: str = "",
        data_source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'DejaVu Sans']
            matplotlib.rcParams['axes.unicode_minus'] = False

            if not filename:
                # 作品说明：秒级时间戳在并发请求下会撞名，附加随机短码。
                filename = f"chart_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}.png"
            filepath = os.path.join(self.output_dir, filename)

            fig, ax = plt.subplots(figsize=(10, 6))

            if chart_type == 'line':
                ax.plot(x_data, y_data, marker='o', linewidth=2, color='#2196F3')
                ax.fill_between(range(len(x_data)), y_data, alpha=0.1, color='#2196F3')
            elif chart_type == 'bar':
                bars = ax.bar(x_data, y_data, color='#2196F3', edgecolor='white')
                for bar, val in zip(bars, y_data):
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                            f'{val:,.1f}', ha='center', va='bottom', fontsize=9)
            elif chart_type == 'pie':
                ax.pie(y_data, labels=x_data, autopct='%1.1f%%', startangle=90)
            else:
                ax.bar(x_data, y_data, color='#2196F3')

            ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
            if chart_type != 'pie':
                ax.set_xlabel(x_label or "")
                ax.set_ylabel(y_label or "（万元）")
                ax.grid(axis='y', alpha=0.3)
                plt.xticks(rotation=45, ha='right')

            plt.tight_layout()
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            plt.close()

            logger.info(f"图表已保存: {filepath}")
            normalized_chart_type = chart_type if chart_type in {"line", "bar", "pie"} else "bar"
            normalized_x = list(x_data)
            normalized_y = [float(v) for v in y_data]
            unit_label = y_label or "万元"
            echarts_option = self._build_echarts_option(
                chart_type=normalized_chart_type,
                title=title,
                x_data=normalized_x,
                y_data=normalized_y,
                y_label=unit_label,
                series_name=series_name or title,
            )
            chart_data = {
                "chart_type": normalized_chart_type,
                "title": title,
                "x_label": x_label,
                "y_label": unit_label,
                "x_data": normalized_x,
                "y_data": normalized_y,
                "series_name": series_name or title,
                "data_source": dict(data_source or {
                    "kind": "explicit",
                    "detail": "ChartTool x_data/y_data 参数",
                }),
                "option": echarts_option,
            }
            return {
                "status": "success",
                "path": filepath,
                "chart_data": chart_data,
                "echarts_option": echarts_option,
            }

        except Exception as e:
            logger.error(f"生成图表失败: {e}")
            return {"status": "error", "message": str(e)}

    @staticmethod
    def _build_echarts_option(
        chart_type: str,
        title: str,
        x_data: List[str],
        y_data: List[float],
        y_label: str,
        series_name: str,
    ) -> Dict[str, Any]:
        option: Dict[str, Any] = {
            "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
            "tooltip": {"trigger": "item" if chart_type == "pie" else "axis", "confine": True},
            "grid": {"left": 52, "right": 24, "top": 52, "bottom": 52, "containLabel": True},
        }
        if chart_type == "pie":
            option["series"] = [{
                "type": "pie",
                "name": series_name,
                "radius": ["38%", "66%"],
                "data": [{"name": name, "value": value} for name, value in zip(x_data, y_data)],
                "avoidLabelOverlap": True,
                "minAngle": 4,
                "label": {"formatter": "{b}: {d}%"},
            }]
            return option

        option.update({
            "xAxis": {"type": "category", "data": x_data},
            "yAxis": {"type": "value", "name": y_label},
            "series": [{
                "type": "line" if chart_type == "line" else "bar",
                "name": series_name,
                "data": y_data,
                **({"smooth": True, "symbolSize": 7, "areaStyle": {"opacity": 0.08}}
                   if chart_type == "line" else {"barMaxWidth": 36}),
            }],
        })
        if len(x_data) > 10:
            option["dataZoom"] = [{"type": "inside", "start": 0, "end": 80}]
        return option
