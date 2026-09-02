"""Safely migrate ignored local .env files away from legacy COMPOSE_* keys."""

from pathlib import Path

from app.core.config import PROJECT_ROOT

LEGACY_KEY_MAP = {
    "COMPOSE_APP_ENV": "APP_ENV",
    "COMPOSE_APP_GIT_COMMIT": "APP_GIT_COMMIT",
    "COMPOSE_LLM_PROVIDER": "LLM_PROVIDER",
    "COMPOSE_LLM_API_KEY": "LLM_API_KEY",
    "COMPOSE_LLM_BASE_URL": "LLM_BASE_URL",
    "COMPOSE_LLM_MODEL": "LLM_MODEL",
    "COMPOSE_SEARCH_PROVIDER": "SEARCH_PROVIDER",
    "COMPOSE_BAIDU_SEARCH_API_KEY": "BAIDU_SEARCH_API_KEY",
    "COMPOSE_BAIDU_SEARCH_BASE_URL": "BAIDU_SEARCH_BASE_URL",
    "COMPOSE_BAIDU_SEARCH_EDITION": "BAIDU_SEARCH_EDITION",
    "COMPOSE_BAIDU_SEARCH_MAX_RESULTS": "BAIDU_SEARCH_MAX_RESULTS",
    "COMPOSE_BAIDU_SEARCH_TIMEOUT_SECONDS": "BAIDU_SEARCH_TIMEOUT_SECONDS",
    "COMPOSE_EMBEDDING_PROVIDER": "EMBEDDING_PROVIDER",
    "COMPOSE_EMBEDDING_MODEL_PATH": "EMBEDDING_MODEL_PATH",
    "COMPOSE_EMBEDDING_MODEL_NAME": "EMBEDDING_MODEL_NAME",
}


def migrate_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    """Prefer canonical values, fill empty/missing values, and never print secrets."""
    values = _values(lines)
    replacements = {
        canonical: values[legacy]
        for legacy, canonical in LEGACY_KEY_MAP.items()
        if values.get(legacy) and not values.get(canonical)
    }
    migrated: list[str] = []
    changed: list[str] = []
    seen_canonical: set[str] = set()
    for raw_line in lines:
        key = _key(raw_line)
        if key in LEGACY_KEY_MAP:
            changed.append(key)
            continue
        if key in replacements:
            migrated.append(f"{key}={replacements[key]}\n")
            seen_canonical.add(key)
        else:
            migrated.append(raw_line)
            if key is not None:
                seen_canonical.add(key)
    for canonical, value in replacements.items():
        if canonical not in seen_canonical:
            migrated.append(f"{canonical}={value}\n")
    return migrated, sorted(set(changed))


def migrate_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    original = path.read_text(encoding="utf-8").splitlines(keepends=True)
    migrated, changed = migrate_lines(original)
    if changed:
        path.write_text("".join(migrated), encoding="utf-8")
    return changed


def _values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        key = _key(line)
        if key is not None:
            values[key] = line.split("=", 1)[1].strip()
    return values


def _key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def main() -> None:
    changed = migrate_file(PROJECT_ROOT / ".env")
    if changed:
        print("migrated legacy keys: " + ", ".join(changed))
    else:
        print("no legacy configuration keys found")


if __name__ == "__main__":
    main()
