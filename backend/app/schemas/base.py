"""Shared strict Pydantic model."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Reject undeclared fields for all API contracts by default."""

    model_config = ConfigDict(extra="forbid")
