"""The engine stays independent of the research platform, Django and PostgreSQL.

The platform (research-platform/) depends on the engine, never the other way round: no engine
module imports them, and the engine's distribution doesn't depend on them.
"""

import ast
import importlib.metadata
import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "src" / "astro_research"
OUTSIDE = {"django", "psycopg", "psycopg2", "research_platform"}


def test_no_engine_module_imports_the_platform_django_or_a_database_driver() -> None:
    imported: dict[str, set[str]] = {}
    for path in sorted(ENGINE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = {node.module.split(".")[0]}
            else:
                continue
            if names & OUTSIDE:
                imported.setdefault(path.name, set()).update(names & OUTSIDE)
    assert imported == {}


def test_the_engine_distribution_depends_on_neither() -> None:
    requirements = importlib.metadata.requires("astro-research") or []
    names = {re.split(r"[\s<>=!~;\[]", requirement)[0].lower() for requirement in requirements}
    assert names & {"django", "psycopg", "psycopg-binary", "research-platform"} == set()
