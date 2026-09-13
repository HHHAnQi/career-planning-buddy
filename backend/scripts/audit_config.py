"""Audit deployable configuration templates without reading secret values."""

import re
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings

DEPLOYMENT_ONLY_KEYS = {
    "EMBEDDING_MODEL_HOST_PATH",
    "FRONTEND_PORT",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "VITE_API_BASE_URL",
}
SENSITIVE_MARKERS = ("API_KEY", "PASSWORD", "SECRET", "TOKEN")


def audit_configuration(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Return stable configuration errors; an empty list means the contract is complete."""
    template_path = project_root / ".env.example"
    compose_path = project_root / "compose.yaml"
    template_keys = _env_keys(template_path)
    settings_keys = {name.upper() for name in Settings.model_fields}
    compose_environment_keys = _compose_environment_keys(compose_path)
    errors: list[str] = []

    for key in sorted(settings_keys - template_keys):
        errors.append(f".env.example is missing Settings field {key}")
    allowed = settings_keys | DEPLOYMENT_ONLY_KEYS
    for key in sorted(template_keys - allowed):
        errors.append(f".env.example contains unknown field {key}")
    for key in sorted(key for key in template_keys if key.startswith("COMPOSE_")):
        errors.append(f"legacy duplicate field is not allowed: {key}")
    for key in sorted(template_keys):
        if key.startswith("VITE_") and any(marker in key for marker in SENSITIVE_MARKERS):
            errors.append(f"browser-visible field cannot contain a secret: {key}")

    compose_text = compose_path.read_text(encoding="utf-8")
    if "${COMPOSE_" in compose_text:
        errors.append("compose.yaml still references legacy COMPOSE_* variables")
    for key in sorted(settings_keys - compose_environment_keys):
        errors.append(f"compose backend environment is missing Settings field {key}")

    compose_references = _compose_references(compose_path)
    embedding_override = project_root / "compose.embedding.yaml"
    if embedding_override.exists():
        override_text = embedding_override.read_text(encoding="utf-8")
        if "${COMPOSE_" in override_text:
            errors.append("compose.embedding.yaml references legacy COMPOSE_* variables")
        compose_references.update(_compose_references(embedding_override))
    for key in sorted(compose_references - template_keys):
        errors.append(f"Compose references field missing from .env.example: {key}")
    return errors


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in keys:
            raise ValueError(f"duplicate configuration field: {key}")
        keys.add(key)
    return keys


def _compose_environment_keys(path: Path) -> set[str]:
    """Collect explicit environment keys without parsing or resolving secret values."""
    return {
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^\s{6}([A-Z][A-Z0-9_]+):", line))
    }


def _compose_references(path: Path) -> set[str]:
    return set(
        re.findall(r"\$\{([A-Z][A-Z0-9_]+)(?::[?+-][^}]*)?}", path.read_text(encoding="utf-8"))
    )


def main() -> None:
    errors = audit_configuration()
    if errors:
        raise SystemExit("\n".join(errors))
    print("configuration audit passed")


if __name__ == "__main__":
    main()
