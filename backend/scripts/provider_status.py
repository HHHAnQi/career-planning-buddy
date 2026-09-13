"""Print secret-free Provider configuration status for deployment diagnostics."""

from app.core.config import get_settings
from app.core.provider_status import build_provider_configuration_status


def main() -> None:
    status = build_provider_configuration_status(get_settings())
    print(status.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
