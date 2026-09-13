"""Provider-neutral LLM request, response, capability, and telemetry contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import StrictModel

LLMRole = Literal["system", "user", "assistant", "tool"]
ReasoningPolicy = Literal["off", "auto"]
StructuredOutputMode = Literal["none", "json_object"]


class LLMMessage(StrictModel):
    role: LLMRole
    content: str


class LLMToolDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    description: str
    input_json_schema: dict[str, object]
    strict: bool = True


class LLMRequest(StrictModel):
    """Canonical request used by project use cases, never a vendor wire payload."""

    operation: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    messages: list[LLMMessage] = Field(min_length=1)
    tools: list[LLMToolDefinition] = Field(default_factory=list)
    tool_choice: Literal["auto", "none"] = "none"
    structured_output: StructuredOutputMode = "none"
    reasoning: ReasoningPolicy = "auto"
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class LLMToolCall(StrictModel):
    call_id: str
    name: str
    arguments: dict[str, object]


class LLMUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)


class LLMResponse(StrictModel):
    content: str | None = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    provider_id: str
    model_id: str
    request_id: str | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    latency_ms: int = Field(ge=0)
    raw_output_hash: str = Field(min_length=64, max_length=64)


class LLMProviderProfile(StrictModel):
    """Capabilities and wire-level compatibility for one configured Provider."""

    provider_id: str
    protocol: Literal["openai_chat"] = "openai_chat"
    supports_tools: bool = True
    supports_json_object: bool = True
    supports_reasoning_control: bool = False
    reasoning_parameter: Literal["thinking"] | None = None


class LLMCallTelemetry(StrictModel):
    event: Literal["llm.call.completed", "llm.call.failed"]
    operation: str
    provider_id: str
    model_id: str
    trace_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    error_code: str | None = None
