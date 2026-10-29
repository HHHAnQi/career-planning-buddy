from __future__ import annotations

import ast

from app.core.config import BACKEND_ROOT


def test_api_modules_do_not_execute_sqlalchemy_queries() -> None:
    """Routers own HTTP only; database statements belong in repositories."""

    violations: list[str] = []
    for path in sorted((BACKEND_ROOT / "app/api").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
                imported = {alias.name for alias in node.names}
                if imported & {"select", "insert", "update", "delete"}:
                    violations.append(f"{path.name}:{node.lineno}: SQL statement import")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "session"
            ):
                violations.append(f"{path.name}:{node.lineno}: session.execute")

    assert violations == []


def test_planning_runtime_support_remains_pure() -> None:
    """Runtime transformations must not grow orchestration or I/O dependencies."""

    path = BACKEND_ROOT / "app/agent/runtime_support.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_prefixes = (
        "app.agent.graph",
        "app.core.database",
        "app.models",
        "app.providers",
        "app.repositories",
        "sqlalchemy",
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith(forbidden_prefixes):
                violations.append(f"{node.module}:{node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(forbidden_prefixes):
                    violations.append(f"{alias.name}:{node.lineno}")

    assert violations == []
