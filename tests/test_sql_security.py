import pytest
from src.agent.sql_guard import normalize_readonly_sql, query_lineage

@pytest.mark.parametrize('sql', [
    'SELECT SLEEP(10)', "SELECT LOAD_FILE('/etc/passwd')", "SELECT GET_LOCK('x',3)",
    'SELECT BENCHMARK(100000,SHA1(1))', "SELECT * FROM income_sheet INTO OUTFILE '/tmp/x'",
    'SELECT * FROM income_sheet FOR UPDATE', 'SELECT @@version', 'SELECT @x := 1',
    'SELECT * FROM mysql.user', 'SELECT 1; DROP TABLE income_sheet',
    'WITH RECURSIVE t AS (SELECT 1 UNION ALL SELECT 1 FROM t) SELECT * FROM t',
    'SELECT /*!50000 SLEEP(10) */ 1', 'SELECT 1 LIMIT 1 OFFSET 10001',
])
def test_dangerous_sql_rejected_without_execution(sql):
    clean, reason = normalize_readonly_sql(sql)
    assert clean is None, clean
    assert reason

@pytest.mark.parametrize('suffix', ['LIMIT 0,999999', 'LIMIT 999999 OFFSET 0', ''])
def test_limit_is_bounded_for_mysql_syntax(suffix):
    clean, reason = normalize_readonly_sql('SELECT * FROM income_sheet '+suffix)
    assert clean is not None, reason
    assert 'LIMIT 500' in clean if suffix else 'LIMIT 50' in clean


def test_comments_inside_literal_and_safe_financial_functions_survive():
    clean, reason = normalize_readonly_sql("SELECT 'a--b/*c*/' AS label, ROUND(AVG(net_profit),2) FROM income_sheet WHERE report_year=2024 AND report_period='FY'")
    assert clean is not None, reason
    assert "a--b/*c*/" in clean


def test_alias_lineage_is_direct_only():
    lineage = query_lineage("SELECT net_profit AS profit, net_profit*2 AS invented FROM income_sheet WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'")
    assert lineage['scope'] == dict(stock_code='603259', report_year='2024', report_period='FY')
    assert lineage['column_lineage'] == {'profit':dict(table='income_sheet',field='net_profit')}
    assert not query_lineage("SELECT net_profit FROM income_sheet WHERE stock_code='603259' OR stock_code='300347'")['scope']


def test_missing_parser_fails_closed(monkeypatch):
    import src.agent.sql_guard as guard
    monkeypatch.setattr(guard,'sqlglot',None)
    assert guard.normalize_readonly_sql('SELECT 1')[0] is None


def test_agent_account_never_falls_back_to_admin(monkeypatch):
    from config.db_config import get_readonly_db_config
    monkeypatch.delenv('SQL_DB_USER', raising=False)
    monkeypatch.delenv('SQL_DB_PASSWORD', raising=False)
    with pytest.raises(RuntimeError): get_readonly_db_config()
    monkeypatch.setenv('SQL_DB_USER','root')
    monkeypatch.setenv('SQL_DB_PASSWORD','example')
    with pytest.raises(RuntimeError): get_readonly_db_config()


def test_direct_query_retains_identity_when_model_only_selects_metric():
    clean,reason=normalize_readonly_sql("SELECT total_operating_revenue FROM income_sheet WHERE stock_abbr='晨光医疗' AND report_year=2024 AND report_period='FY'")
    assert clean is not None,reason
    assert all(field in clean.split('FROM')[0] for field in ('stock_code','stock_abbr','report_year','report_period'))
    aggregate,_=normalize_readonly_sql('SELECT SUM(net_profit) FROM income_sheet')
    assert 'stock_code' not in aggregate
