"""作品说明：通过 SQL 语法树限制只读 SELECT，校验失败时阻断。"""
from __future__ import annotations
import re
from typing import Optional, Tuple
from .domain import ALLOWED_TABLES
try:
    import sqlglot
    from sqlglot import exp
except ImportError:
    sqlglot = None
    exp = None

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_OFFSET = 10000
MAX_SQL_LENGTH = 16000
SAFE_FUNCTIONS = frozenset({
    'ABS', 'AVG', 'SUM', 'MIN', 'MAX', 'COUNT', 'ROUND', 'FLOOR', 'CEIL', 'CEILING',
    'COALESCE', 'IF', 'IFNULL', 'NULLIF', 'CAST', 'TRY_CAST', 'EXTRACT',
    'YEAR', 'MONTH', 'DAY', 'DATE', 'DATE_FORMAT', 'STR_TO_DATE', 'TIME_TO_STR',
    'CONCAT', 'CONCAT_WS', 'LOWER', 'UPPER', 'TRIM', 'SUBSTRING', 'LENGTH', 'CHAR_LENGTH',
    'LAG', 'LEAD', 'ROW_NUMBER', 'RANK', 'DENSE_RANK', 'FIRST_VALUE', 'LAST_VALUE',
    'GREATEST', 'LEAST', 'POWER', 'SQRT', 'MOD', 'PERCENT_RANK', 'CASE', 'AND', 'OR', 'FIELD',
})

def _strip_wrappers(sql: str) -> str:
    # 作品说明：SQL 字符串中的注释符号属于数据，不能按注释删除。
    cleaned = str(sql or '').strip()
    fenced = re.fullmatch(r'```(?:sql|mysql)?\s*\n?([\s\S]*?)\n?```', cleaned, re.I)
    return (fenced.group(1) if fenced else cleaned).strip()

def _integer(node, label: str) -> int:
    if not isinstance(node, exp.Literal) or node.is_string or not re.fullmatch(r'\d+', node.this):
        raise ValueError(f'{label} must be a nonnegative integer literal')
    return int(node.this)

def _parse_validate(sql: str):
    if sqlglot is None or exp is None:
        raise ValueError('sqlglot AST parser required; SQL execution disabled')
    if len(sql) > MAX_SQL_LENGTH:
        raise ValueError('SQL exceeds length limit')
    if '/*!' in sql or '/*+' in sql:
        raise ValueError('Executable comments and query hints are not allowed')
    statements = [s for s in sqlglot.parse(sql, read='mysql') if s is not None]
    if len(statements) != 1:
        raise ValueError('Only one SQL statement is allowed')
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union)):
        raise ValueError('Only SELECT / WITH queries are allowed')
    forbidden = tuple(getattr(exp, name) for name in (
        'Insert', 'Update', 'Delete', 'Drop', 'Create', 'Alter', 'Command', 'Into',
        'Lock', 'Parameter', 'SessionParameter', 'PropertyEQ', 'Set', 'Transaction',
    ) if hasattr(exp, name))
    if any(isinstance(node, forbidden) for node in root.walk()):
        raise ValueError('Write operations, variables and locking are forbidden')
    for node in root.find_all(exp.With):
        if node.args.get('recursive'):
            raise ValueError('Recursive queries are not allowed')
    for func in root.find_all(exp.Func):
        name = str(func.name if isinstance(func, exp.Anonymous) else func.sql_name()).upper()
        if name not in SAFE_FUNCTIONS:
            raise ValueError(f'Function is not allowed: {name}')
    ctes = {str(cte.alias_or_name).lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        if table.db or table.catalog:
            raise ValueError('Cross-schema queries are forbidden')
        name = str(table.name or '').lower()
        if name not in ALLOWED_TABLES and name not in ctes:
            raise ValueError(f'Table is not allowed: {name}')
    for limit in root.find_all(exp.Limit):
        _integer(limit.expression, 'LIMIT')
    for offset in root.find_all(exp.Offset):
        if _integer(offset.expression, 'OFFSET') > MAX_OFFSET:
            raise ValueError(f'OFFSET exceeds {MAX_OFFSET}')
    return root

def normalize_readonly_sql(sql: str) -> Tuple[Optional[str], str]:
    cleaned = _strip_wrappers(sql)
    if not cleaned:
        return None, 'SQL is empty'
    try:
        root = _parse_validate(cleaned)
        # 作品说明：事实查询必须返回数据库身份字段。
        from_clause = root.args.get('from_')
        table = from_clause.this if from_clause else None
        if (isinstance(root, exp.Select) and isinstance(table, exp.Table) and table.name in ALLOWED_TABLES
                and not any(root.args.get(key) for key in ('joins', 'with_', 'distinct', 'group'))
                and root.expressions and all(isinstance(item, exp.Column) for item in root.expressions)):
            projected = {item.alias_or_name for item in root.expressions}
            for field in ('stock_code', 'stock_abbr', 'report_year', 'report_period'):
                if field not in projected:
                    root.select(exp.column(field, table=table.alias or None), append=True, copy=False)
        limit = root.args.get('limit')
        count = min(_integer(limit.expression, 'LIMIT'), MAX_LIMIT) if limit else DEFAULT_LIMIT
        root.set('limit', exp.Limit(expression=exp.Literal.number(count)))
        return root.sql(dialect='mysql', comments=False), ''
    except Exception as exc:
        return None, f'SQL validation failed: {exc}'

def query_lineage(sql: str) -> dict:
    """作品说明：仅允许可追踪的直接列来源与顶层合取常量。"""
    result = {'scope': {}, 'column_lineage': {}}
    try:
        root = _parse_validate(sql)
        if not isinstance(root, exp.Select) or root.args.get('with_') or root.args.get('joins'):
            return result
        from_clause = root.args.get('from_')
        table = from_clause.this if from_clause else None
        if not isinstance(table, exp.Table):
            return result
        from .domain import get_table_fields
        fields = get_table_fields().get(table.name, {})
        for selected in root.expressions:
            value = selected.this if isinstance(selected, exp.Alias) else selected
            if isinstance(value, exp.Star):
                for name in fields:
                    result['column_lineage'][name] = {'table': table.name, 'field': name}
            elif isinstance(value, exp.Column) and value.name in fields:
                result['column_lineage'][selected.alias_or_name] = {'table': table.name, 'field': value.name}
        where = root.args.get('where')
        def terms(node):
            if isinstance(node, exp.And):
                return terms(node.this) + terms(node.expression)
            if isinstance(node, exp.Paren):
                return terms(node.this)
            return [node]
        bindings = {}
        for term in terms(where.this) if where else []:
            if not isinstance(term, exp.EQ):
                continue
            col, literal = term.this, term.expression
            if isinstance(literal, exp.Column):
                col, literal = literal, col
            if isinstance(col, exp.Column) and isinstance(literal, exp.Literal) and col.name in {
                'stock_code', 'stock_abbr', 'report_year', 'report_period'
            }:
                bindings.setdefault(col.name, set()).add(str(literal.this))
        result['scope'] = {key: next(iter(values)) for key, values in bindings.items() if len(values) == 1}
    except Exception:
        pass
    return result
