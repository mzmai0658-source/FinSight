from pathlib import Path

from src.agent.chart_tool import ChartTool


def test_chart_tool_returns_echarts_option_while_preserving_png(tmp_path: Path):
    tool = ChartTool(output_dir=str(tmp_path))

    result = tool.run(
        chart_type="line",
        title="Revenue Trend",
        x_data=["2022", "2023", "2024"],
        y_data=[10.0, 12.5, 15.0],
        y_label="万元",
        series_name="Revenue",
        filename="revenue.png",
    )

    assert result["status"] == "success"
    assert (tmp_path / "revenue.png").exists()
    option = result["chart_data"]["option"]
    assert option["xAxis"]["data"] == ["2022", "2023", "2024"]
    assert option["yAxis"]["name"] == "万元"
    assert option["series"][0]["type"] == "line"
    assert option["series"][0]["data"] == [10.0, 12.5, 15.0]
    assert result["echarts_option"] == option
    assert result["chart_data"]["data_source"]["kind"] == "explicit"


def test_chart_data_unit_label_matches_axis_when_omitted(tmp_path: Path):
    """作品说明：y_label 省略时，chart_data 与坐标轴必须给出同一个单位，不能各说一套。"""
    tool = ChartTool(output_dir=str(tmp_path))

    result = tool.run(
        chart_type="bar",
        title="Revenue",
        x_data=["2023", "2024"],
        y_data=[1.0, 2.0],
        filename="unit.png",
    )

    chart_data = result["chart_data"]
    assert chart_data["y_label"] == chart_data["option"]["yAxis"]["name"]


def test_generated_filenames_do_not_collide_within_same_second(tmp_path: Path):
    """作品说明：并发请求会在同一秒内落图，仅靠秒级时间戳会互相覆盖。"""
    tool = ChartTool(output_dir=str(tmp_path))

    paths = set()
    for _ in range(5):
        result = tool.run(
            chart_type="line",
            title="Revenue",
            x_data=["2023", "2024"],
            y_data=[1.0, 2.0],
        )
        assert result["status"] == "success"
        paths.add(result["path"])

    assert len(paths) == 5


def test_chart_data_preserves_explicit_source(tmp_path: Path):
    tool = ChartTool(output_dir=str(tmp_path))
    source = {
        "kind": "sql_result",
        "query_index": 1,
        "sql": "SELECT report_year, revenue FROM income_sheet",
        "detail": "report_year → revenue",
    }

    result = tool.run(
        chart_type="line",
        title="Revenue",
        x_data=["2023", "2024"],
        y_data=[1.0, 2.0],
        data_source=source,
    )

    assert result["status"] == "success"
    assert result["chart_data"]["data_source"] == source
