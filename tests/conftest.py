"""作品说明：显式离线公司目录样例使单元测试无需私有数据库或表格。"""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.utils import company_registry as registry

# 作品说明：公开名称和代码仅作为测试身份，测试金额属于构造样例。
registry._CODE_TO_NAME.clear()
registry._NAME_TO_CODE.clear()
for code,name in [('603259','药明康德'),('300347','泰格医药'),('990001','星河医药'),('990002','晨光医疗'),('990003','青禾生物'),('990004','远山诊断'),('990005','云杉科技')]:
    registry._register(code,name)
registry._LOADED=True
