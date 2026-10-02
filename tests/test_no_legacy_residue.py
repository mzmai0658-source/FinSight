# -*- coding: utf-8 -*-
"""作品说明：遗留模块检查：在线服务应走通用 Agent 编排，而不是旧的任务脚本或废弃依赖。"""

import re
import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

AGENT_DIR = ROOT_DIR / "src" / "agent"
API_DIR = ROOT_DIR / "src" / "api"
FRONTEND_SRC = ROOT_DIR / "frontend" / "src"

# 作品说明：用转义保存敏感词，避免源码里直接出现这些字面量。
_FORBIDDEN_TERMS = [
    "\u6bd4\u8d5b",
    "\u7ade\u8d5b",
    "\u8d5b\u9898",
    "B\u9898",
    "\u6cf0\u8fea",
    "ted" "dy",
    "competi" "tion",
    "\u9644\u4ef66",
    "\u63d0\u4ea4\u7ed3\u679c",
    "\u8bc4\u5206\u6807\u51c6",
]


def iter_py_files(*dirs: Path):
    for directory in dirs:
        for path in directory.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def iter_frontend_files():
    for pattern in ("*.vue", "*.ts", "*.css", "*.html"):
        for path in FRONTEND_SRC.rglob(pattern):
            if "node_modules" in path.parts:
                continue
            yield path


class TestBackendResidue:
    def test_no_legacy_task_terms_in_online_code(self):
        violations = []
        for path in iter_py_files(AGENT_DIR, API_DIR):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for term in _FORBIDDEN_TERMS:
                if re.search(term, text, re.IGNORECASE):
                    violations.append(f"{path.relative_to(ROOT_DIR)}: {term}")
        assert not violations, "online agent/api still contains legacy wording:\n" + "\n".join(violations)

    def test_legacy_rule_engine_removed(self):
        assert not (AGENT_DIR / "query_agent.py").exists(), "query_agent.py 应已被 orchestrator.py 取代"
        assert not (AGENT_DIR / "chat_planner.py").exists(), "chat_planner.py 应已被移除"
        assert not (AGENT_DIR / ("competi" "tion" "_runner.py")).exists()
        assert not (AGENT_DIR / ("task" "2" "_executor.py")).exists()

    def test_etl_moved_out_of_agent(self):
        for name in ("etl_worker.py", "boss.py", "rag_builder.py"):
            assert not (AGENT_DIR / name).exists(), f"{name} 应已移动到 src/etl"
            assert (ROOT_DIR / "src" / "etl" / name).exists(), f"src/etl/{name} 缺失"

    def test_agent_dependencies_respect_explicit_library_boundary(self):
        allowed = {'langchain_ollama', 'langgraph'}
        violations = []
        for path in iter_py_files(AGENT_DIR, API_DIR):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [node.module or ''] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names] if isinstance(node, ast.Import) else []
                for name in names:
                    root = name.split('.')[0]
                    if root.startswith('langchain') and root not in allowed:
                        violations.append(f'{path.relative_to(ROOT_DIR)}: {name}')
        assert not violations, '\n'.join(violations)

    def test_production_entry_cannot_import_historical_planners(self):
        production = [*iter_py_files(AGENT_DIR / 'v3'), API_DIR / 'main.py']
        forbidden = {'orchestrator','semantic_planner','financial_query','fallback','entity_linker','query_plan'}
        for path in production:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert (node.module or '').split('.')[-1] not in forbidden, f'{path} imports a historical planner'

    def test_no_hardcoded_company_answer_logic(self):
        """作品说明：不允许把特定公司名写死在 agent 决策逻辑里（公司表应来自注册表）。"""
        hardcoded_patterns = [
            r"_answer_insurance_catalog",
            r"insurance_catalog",
            r"医保目录.*硬编码",
        ]
        for path in iter_py_files(AGENT_DIR):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in hardcoded_patterns:
                assert not re.search(pattern, text), f"{path.relative_to(ROOT_DIR)} 包含硬编码答案逻辑: {pattern}"

    def test_new_core_modules_exist(self):
        for name in ("orchestrator.py", "prompts.py", "sql_guard.py", "fallback.py", "domain.py"):
            assert (AGENT_DIR / name).exists(), f"src/agent/{name} 缺失"


class TestFrontendResidue:
    def test_no_ant_design_in_dependencies(self):
        package_json = (ROOT_DIR / "frontend" / "package.json").read_text(encoding="utf-8")
        assert "ant-design-vue" not in package_json
        assert "@ant-design" not in package_json

    def test_no_ant_design_imports_in_source(self):
        for path in iter_frontend_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "ant-design" not in text, f"{path.relative_to(ROOT_DIR)} 仍引用 ant-design"
            assert not re.search(r"<a-[a-z]", text), f"{path.relative_to(ROOT_DIR)} 仍使用 antd 组件"

    def test_no_legacy_task_terms_in_frontend(self):
        for path in iter_frontend_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for term in _FORBIDDEN_TERMS:
                assert term not in text, f"{path.relative_to(ROOT_DIR)} still contains legacy wording: {term}"
