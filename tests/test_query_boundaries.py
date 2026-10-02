from src.agent.facts import extract_report_periods, extract_report_years, metadata_matches, metric_mentions, request_scope, requested_metric_fields
from src.agent.domain import CODE_TO_NAME_MAP, COMPANY_CODE_MAP
from src.agent.orchestrator import (
    _TurnState,
    _deterministic_tool_answer,
    _infer_chart_x_field,
    _resolve_turn_scope,
    _sql_policy_error,
)
from src.agent.verifier import verify_numbers


def test_report_period_nicknames_do_not_split_one_period_into_two():
    assert extract_report_periods("22年一季报净利润") == ["Q1"]
    assert extract_report_periods("22年半年净利润") == ["HY"]
    assert extract_report_periods("22年半年报净利润") == ["HY"]
    assert extract_report_periods("上半年度净利润") == ["HY"]


def test_short_metric_names_and_a_trend_request_are_understood():
    from src.agent.entity_linker import link_entities
    assert link_entities("净利多少").metric_ids() == ["net_profit"]
    assert link_entities("毛利多少").metric_ids() == ["gross_profit_margin"]
    assert link_entities("扣非多少").metric_ids() == []
    assert link_entities("扣非多少").ambiguous_metrics
    assert link_entities("营业额多少").metric_ids() == ["total_operating_revenue"]
    assert link_entities("利润趋势").drawing is True


def test_colloquial_two_digit_years_are_normalized():
    assert extract_report_years("凯莱英23和24年的主要财务指标") == [2023, 2024]
    assert extract_report_years("万邦德22到24这几年营收咋走") == [2022, 2023, 2024]
    assert extract_report_years("23/24年收入对比") == [2023, 2024]
    assert extract_report_years("盘龙药业去年前三季度", current_year=2026) == [2025]
    assert extract_report_years("近三年年报", current_year=2026) == [2023, 2024, 2025]
    assert extract_report_years("把22，23，24和三年的利润画个图") == [2022, 2023, 2024]
    assert extract_report_years("22、23、24年净利润") == [2022, 2023, 2024]
    assert extract_report_years("22 23 24年利润") == [2022, 2023, 2024]
    assert extract_report_years("二〇二二到二〇二四年利润") == [2022, 2023, 2024]
    assert extract_report_years("二〇二四年营收") == [2024]


def test_colloquial_metrics_and_main_numbers_are_structured():
    assert {item[2] for item in metric_mentions("盘龙药业去年赚了多少钱")} == {"net_profit"}
    assert {item[2] for item in metric_mentions("24年收入和负债有多少")} == {
        "total_operating_revenue", "liability_total_liabilities"
    }
    assert requested_metric_fields("凯莱英24年主要数字都看一下") == {
        "total_operating_revenue", "net_profit", "gross_profit_margin", "roe"
    }


def test_report_period_is_exact_and_mixed_periods_keep_year_pairing():
    q3 = request_scope("凯莱英23、24年前三季度表现咋样")
    assert q3["year_periods"] == [(2023, "Q3"), (2024, "Q3")]
    assert metadata_matches(
        {"stock_code": "002821", "report_year": 2024, "report_period": "Q3"}, q3
    )
    assert not metadata_matches(
        {"stock_code": "002821", "report_year": 2024, "report_period": "HY"}, q3
    )

    mixed = request_scope("凯莱英24年上半年跟23年全年相比")
    assert set(mixed["year_periods"]) == {(2024, "HY"), (2023, "FY")}
    assert not metadata_matches(
        {"stock_code": "002821", "report_year": 2023, "report_period": "HY"}, mixed
    )


def test_sql_policy_rejects_scope_expansion_for_short_years_and_periods():
    question, _ = _resolve_turn_scope(
        "可以，看看凯莱英的23和24年的主要财务指标，顺便帮我画个图", None
    )
    broad = (
        "SELECT stock_code, stock_abbr, report_year, report_period, total_operating_revenue "
        "FROM income_sheet WHERE stock_code='002821' ORDER BY report_year LIMIT 50"
    )
    assert "report_year" in _sql_policy_error(question, broad)

    wrong_periods = (
        "SELECT stock_code, stock_abbr, report_year, report_period, total_operating_revenue "
        "FROM income_sheet WHERE stock_code='002821' AND report_year IN (2023, 2024) "
        "AND report_period IN ('Q1','HY','Q3') LIMIT 50"
    )
    assert "报告期" in _sql_policy_error("凯莱英23、24年前三季度营收", wrong_periods)


def test_mixed_period_cross_product_query_is_rejected():
    sql = (
        "SELECT stock_code, stock_abbr, report_year, report_period, total_operating_revenue, net_profit "
        "FROM income_sheet WHERE stock_code='002821' AND report_year IN (2023, 2024) "
        "AND report_period IN ('FY','HY') LIMIT 50"
    )
    error = _sql_policy_error("凯莱英24年上半年跟23年全年相比，营收和净利润变了多少", sql)
    assert "年份—报告期" in error

    exact_pairs = (
        "SELECT stock_code, stock_abbr, report_year, report_period, total_operating_revenue, net_profit "
        "FROM income_sheet WHERE stock_code='002821' AND "
        "((report_year=2024 AND report_period='HY') OR (report_year=2023 AND report_period='FY'))"
    )
    assert _sql_policy_error(
        "凯莱英24年上半年跟23年全年相比，营收和净利润变了多少", exact_pairs
    ) == ""


def test_company_comparison_chart_axis_is_inferred_from_labels():
    rows = [
        {"stock_abbr": "万邦德", "stock_code": "002082", "report_year": 2024, "report_period": "FY"},
        {"stock_abbr": "盘龙药业", "stock_code": "002864", "report_year": 2024, "report_period": "FY"},
    ]
    assert _infer_chart_x_field(["万邦德", "盘龙药业"], rows) == "stock_abbr"

    state = _TurnState("chart", 1, "帮我比比万邦德跟盘龙药业24年全年营收，哪个更高")
    state.chart_data = [{
        "title": "两家公司2024年营业收入对比",
        "x_data": ["万邦德", "盘龙药业"],
        "y_data": [144336.52, 97386.42],
        "y_label": "营业收入（万元）",
    }]
    assert "万邦德更高" in _deterministic_tool_answer(state)


def test_followup_scope_inherits_only_missing_slots(monkeypatch):
    monkeypatch.setitem(COMPANY_CODE_MAP, "万邦德", "002082")
    monkeypatch.setitem(COMPANY_CODE_MAP, "盘龙药业", "002864")
    monkeypatch.setitem(CODE_TO_NAME_MAP, "002082", "万邦德")
    monkeypatch.setitem(CODE_TO_NAME_MAP, "002864", "盘龙药业")
    history = [
        {"role": "user", "content": "先帮我看看万邦德24年营业收入"},
        {"role": "assistant", "content": "万邦德2024年营业收入为144,336.52万元。"},
    ]
    resolved, instruction = _resolve_turn_scope("那盘龙药业呢？", history)
    scope = request_scope(resolved)
    assert scope["stock_codes"] == ["002864"]
    assert scope["year_periods"] == [(2024, "FY")]
    assert "total_operating_revenue" in instruction


def test_followup_year_comparison_inherits_q3_instead_of_switching_to_half_year(monkeypatch):
    monkeypatch.setitem(COMPANY_CODE_MAP, "凯莱英", "002821")
    monkeypatch.setitem(CODE_TO_NAME_MAP, "002821", "凯莱英")
    history = [
        {"role": "user", "content": "凯莱英24年前三季度营收多少"},
        {"role": "assistant", "content": "凯莱英2024年前三季度营业收入已查询。"},
    ]
    resolved, _ = _resolve_turn_scope("那跟23年比呢？", history)
    assert set(request_scope(resolved)["year_periods"]) == {(2023, "Q3"), (2024, "Q3")}


def test_main_metrics_return_available_year_and_explain_missing_year_without_chart(monkeypatch):
    monkeypatch.setitem(COMPANY_CODE_MAP, "凯莱英", "002821")
    monkeypatch.setitem(CODE_TO_NAME_MAP, "002821", "凯莱英")
    question = "看看凯莱英的23和24年的主要财务指标，顺便帮我画个图"
    resolved, _ = _resolve_turn_scope(question, None)
    state = _TurnState("scope", 1, question)
    state.scope_question = resolved
    state.sql_rows = [
        {
            "stock_code": "002821",
            "stock_abbr": "凯莱英",
            "report_year": 2023,
            "report_period": "FY",
            "total_operating_revenue": 782519.03,
            "net_profit": 226881.04,
            "gross_profit_margin": 51.1603,
            "roe": 13.66,
        }
    ]

    answer = _deterministic_tool_answer(state)

    assert "| 公司 | 年份 | 报告期 | 指标 | 数值 |" in answer
    assert "| 凯莱英 | 2023年 | 全年 | 营业收入 | 782,519.03万元 |" in answer
    assert "| 凯莱英 | 2024年 | 全年 | 主要财务指标 | 未入库 |" in answer
    assert "不生成可能误导的对比图" in answer
    assert verify_numbers(answer, state.sql_rows, question=resolved)["status"] == "pass"


def test_followup_answer_uses_current_sql_row_instead_of_previous_assistant_number(monkeypatch):
    monkeypatch.setitem(COMPANY_CODE_MAP, "万邦德", "002082")
    monkeypatch.setitem(COMPANY_CODE_MAP, "盘龙药业", "002864")
    monkeypatch.setitem(CODE_TO_NAME_MAP, "002082", "万邦德")
    monkeypatch.setitem(CODE_TO_NAME_MAP, "002864", "盘龙药业")
    history = [
        {"role": "user", "content": "先帮我看看万邦德24年营业收入"},
        {"role": "assistant", "content": "万邦德2024年营业收入为144,336.52万元。"},
    ]
    resolved, _ = _resolve_turn_scope("那盘龙药业呢？", history)
    state = _TurnState("scope", 1, "那盘龙药业呢？")
    state.scope_question = resolved
    state.sql_rows = [{
        "stock_code": "002864",
        "stock_abbr": "盘龙药业",
        "report_year": 2024,
        "report_period": "FY",
        "total_operating_revenue": 97386.42,
    }]

    answer = _deterministic_tool_answer(state)

    assert "盘龙药业2024年全年营业收入为 97,386.42万元" in answer
    assert "144,336.52" not in answer
    assert verify_numbers(answer, state.sql_rows, question=resolved)["status"] == "pass"


def test_main_metrics_chart_summary_does_not_repeat_ambiguous_numbers(monkeypatch):
    monkeypatch.setitem(COMPANY_CODE_MAP, "凯莱英", "002821")
    monkeypatch.setitem(CODE_TO_NAME_MAP, "002821", "凯莱英")
    question = "凯莱英23、24年前三季度主要财务指标"
    resolved, _ = _resolve_turn_scope(question, None)
    state = _TurnState("scope", 1, question)
    state.scope_question = resolved
    state.sql_rows = [
        {"stock_code": "002821", "stock_abbr": "凯莱英", "report_year": year, "report_period": "Q3",
         "total_operating_revenue": revenue, "net_profit": profit, "gross_profit_margin": margin, "roe": roe}
        for year, revenue, profit, margin, roe in [
            (2023, 638305.71, 221010.57, 54.1272, 3.18),
            (2024, 414028.86, 71032.51, 43.6046, 1.28),
        ]
    ]
    state.chart_data = [{
        "title": "凯莱英2023—2024年前三季度营业收入",
        "x_data": ["2023年", "2024年"],
        "y_data": [638305.71, 414028.86],
        "y_label": "营业收入（万元）",
    }]

    answer = _deterministic_tool_answer(state)

    assert answer.count("638,305.71万元") == 1
    assert answer.count("414,028.86万元") == 1
    assert "已生成《凯莱英2023—2024年前三季度营业收入》" in answer
    assert verify_numbers(answer, state.sql_rows, question=resolved)["status"] == "pass"
