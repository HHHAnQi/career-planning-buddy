"""Secret-free runtime configuration diagnostics."""

from pydantic import Field

from app.schemas.base import StrictModel


class ProviderConfigurationItem(StrictModel):
    provider: str
    configured: bool
    real: bool
    missing_fields: list[str] = Field(default_factory=list)


class ProviderConfigurationStatus(StrictModel):
    app_env: str
    eval_provider_mode: str
    ready: bool
    providers: dict[str, ProviderConfigurationItem]
    warnings: list[str] = Field(default_factory=list)
