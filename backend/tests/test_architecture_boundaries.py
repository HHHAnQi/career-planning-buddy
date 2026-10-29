from __future__ import annotations

import ast
from pathlib import Path

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
