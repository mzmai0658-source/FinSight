"""作品说明：创建隔离的只读查询账号，将凭据私下保存。"""
import argparse
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv, set_key
    import pymysql
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env.demo')
    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)
    from config.db_config import get_db_config
    config = get_db_config()
    from scripts.bootstrap_demo_v3 import allowed_database
    if not allowed_database(config.database, config.host):
        raise RuntimeError('Account bootstrap is restricted to the local finsight_demo database')
    user=os.getenv('SQL_DB_USER') or 'fs_demo_r_' + secrets.token_hex(4)
    password=os.getenv('SQL_DB_PASSWORD') or secrets.token_urlsafe(36)
    import re
    if not re.fullmatch(r'fs_demo_r_[a-zA-Z0-9_]+',user):
        raise RuntimeError('演示只读账户必须使用 fs_demo_r_ 前缀，不修改其他账户权限')
    with pymysql.connect(host=config.host, port=config.port, user=config.user,
                         password=config.password, database=config.database) as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s', (user, 'localhost', password))
            cursor.execute('REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@%s', (user, 'localhost'))
            for table in ('company', 'income_sheet', 'balance_sheet', 'cash_flow_sheet', 'core_performance_indicators_sheet',
                          'financial_data_releases', 'financial_data_pointer', 'financial_canonical_reports', 'financial_canonical_facts',
                          'financial_report_provenance', 'financial_report_versions'):
                cursor.execute(f'GRANT SELECT ON `{config.database}`.`{table}` TO %s@%s', (user, 'localhost'))
    set_key(str(args.env_file), 'SQL_DB_USER', user)
    set_key(str(args.env_file), 'SQL_DB_PASSWORD', password)
    print('Isolated demo reader created; credentials stored in the private env file.')


if __name__ == '__main__':
    main()
