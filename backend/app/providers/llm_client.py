"""Unified OpenAI Chat wire adapter with normalized errors, usage, and telemetry."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
from time import monotonic
from typing import Protocol

import httpx

from app.agent.errors import (
    AgentError,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    parse_retry_after,
)
from app.core.config import Settings
from app.core.telemetry import current_telemetry_context
from app.providers.llm_contracts import (
    LLMCallTelemetry,
    LLMProviderProfile,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    LLMUsage,
)
from app.providers.llm_profiles import resolve_provider_profile

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def aclose(self) -> None: ...


# Async receiver of one streamed text delta (wire-level token streaming).
StreamDeltaCallback = Callable[[str], Awaitable[None]]


class LLMTelemetrySink(Protocol):
    def emit(self, event: LLMCallTelemetry) -> None: ...


class StructuredLogLLMTelemetrySink:
    """Safe default sink; raw prompts, responses, and credentials are never logged."""

    def emit(self, event: LLMCallTelemetry) -> None:
        level = logging.INFO if event.event == "llm.call.completed" else logging.WARNING
        logger.log(level, event.event, extra=event.model_dump(exclude_none=True))


class OpenAIChatLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        profile: LLMProviderProfile,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        telemetry: LLMTelemetrySink | None = None,
    ) -> None:
        if not api_key or not base_url:
            raise ProviderConfigurationError("LLM client requires API key and base URL")
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._profile = profile
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        self._telemetry = telemetry or StructuredLogLLMTelemetrySink()

    @property
    def provider_id(self) -> str:
        return self._profile.provider_id

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = monotonic()
        try:
            body = self._request_body(request)
            response = await self._client.post(self._endpoint, json=body)
            self._raise_for_status(response)
            response_text = response.text
            payload = self._mapping(response)
            normalized = self._normalize(
                request=request,
                payload=payload,
                response=response,
                response_text=response_text,
                latency_ms=int((monotonic() - started) * 1000),
            )
        except httpx.TimeoutException as exc:
            error: AgentError = ProviderTimeoutError(
                "LLM provider request timed out", retryable=True
            )
            self._emit_failure(request, started, error)
            raise error from exc
        except httpx.RequestError as exc:
            error = ProviderUnavailableError(
                "LLM provider could not be reached", retryable=True
            )
            self._emit_failure(request, started, error)
            raise error from exc
        except AgentError as exc:
            self._emit_failure(request, started, exc)
            raise
        except (TypeError, ValueError) as exc:
            error = ProviderUnavailableError(
                "LLM provider returned an invalid response", retryable=False
            )
            self._emit_failure(request, started, error)
            raise error from exc
        self._emit_success(request, normalized)
        return normalized

    async def complete_streamed(
        self,
        request: LLMRequest,
        *,
        on_delta: StreamDeltaCallback | None = None,
    ) -> LLMResponse:
        """Stream one Chat Completion over SSE and return the assembled response.

        Same contract as ``complete``: normalized ``LLMResponse`` (accumulated
        content, assembled tool calls, usage), typed errors, and telemetry.
        ``on_delta`` receives every content text fragment as it arrives; it is
        never given tool-call or reasoning fragments.
        """
        started = monotonic()
        try:
            body = {
                **self._request_body(request),
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            content_parts: list[str] = []
            tool_fragments: dict[int, dict[str, str]] = {}
            usage_raw: object = None
            finish_reason: str | None = None
            model_id: str | None = None
            response_id: str | None = None
            async with self._client.stream("POST", self._endpoint, json=body) as response:
                self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    chunk: object = json.loads(data)
                    if not isinstance(chunk, Mapping):
                        raise ValueError("stream chunk is not an object")
                    if usage_raw is None:
                        usage_raw = chunk.get("usage")
                    model_value = chunk.get("model")
                    if model_id is None and isinstance(model_value, str):
                        model_id = model_value
                    id_value = chunk.get("id")
                    if response_id is None and isinstance(id_value, str):
                        response_id = id_value
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    first = choices[0]
                    if not isinstance(first, Mapping):
                        continue
                    reason = first.get("finish_reason")
                    if isinstance(reason, str):
                        finish_reason = reason
                    delta = first.get("delta")
                    if not isinstance(delta, Mapping):
                        continue
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        content_parts.append(text)
                        if on_delta is not None:
                            await on_delta(text)
                    calls = delta.get("tool_calls")
                    if isinstance(calls, list):
                        for call in calls:
                            if not isinstance(call, Mapping):
                                continue
                            index = call.get("index")
                            if not isinstance(index, int):
                                continue
                            fragment = tool_fragments.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            call_id = call.get("id")
                            if isinstance(call_id, str):
                                fragment["id"] += call_id
                            function = call.get("function")
                            if isinstance(function, Mapping):
                                name = function.get("name")
                                if isinstance(name, str):
                                    fragment["name"] += name
                                arguments = function.get("arguments")
                                if isinstance(arguments, str):
                                    fragment["arguments"] += arguments
            accumulated = "".join(content_parts)
            content = accumulated.strip() or None
            synthesized_calls: list[Mapping[str, object]] = [
                {
                    "id": fragment["id"] or None,
                    "function": {
                        "name": fragment["name"],
                        "arguments": fragment["arguments"],
                    },
                }
                for _, fragment in sorted(tool_fragments.items())
            ]
            tool_calls = self._tool_calls(synthesized_calls) if synthesized_calls else []
            if content is None and not tool_calls:
                raise ValueError("response has neither content nor tool calls")
            normalized = LLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                provider_id=self.provider_id,
                model_id=model_id or request.model,
                request_id=response_id,
                usage=self._usage(usage_raw),
                latency_ms=int((monotonic() - started) * 1000),
                # Streaming hashes the assembled content (the raw SSE body is
                # never fully buffered); stable for the same token sequence.
                raw_output_hash=sha256(accumulated.encode("utf-8")).hexdigest(),
            )
        except httpx.TimeoutException as exc:
            error: AgentError = ProviderTimeoutError(
                "LLM provider stream timed out", retryable=True
            )
            self._emit_failure(request, started, error)
            raise error from exc
        except httpx.RequestError as exc:
            error = ProviderUnavailableError(
                "LLM provider could not be reached", retryable=True
            )
            self._emit_failure(request, started, error)
            raise error from exc
        except AgentError as exc:
            self._emit_failure(request, started, exc)
            raise
        except (TypeError, ValueError) as exc:
            error = ProviderUnavailableError(
                "LLM provider returned an invalid stream", retryable=False
            )
            self._emit_failure(request, started, error)
            raise error from exc
        self._emit_success(request, normalized)
        return normalized

    def _request_body(self, request: LLMRequest) -> dict[str, object]:
        if request.tools and not self._profile.supports_tools:
            raise ProviderConfigurationError(
                f"{self.provider_id} does not support tool calling"
            )
        if request.structured_output == "json_object" and not self._profile.supports_json_object:
            raise ProviderConfigurationError(
                f"{self.provider_id} does not support JSON object output"
            )
        body: dict[str, object] = {
            "model": request.model,
            "messages": [message.model_dump(mode="json") for message in request.messages],
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            body["max_tokens"] = request.max_output_tokens
        if request.structured_output == "json_object":
            body["response_format"] = {"type": "json_object"}
        if request.reasoning == "off" and self._profile.supports_reasoning_control:
            if self._profile.reasoning_parameter == "thinking":
                body["thinking"] = {"type": "disabled"}
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_json_schema,
                        "strict": tool.strict,
                    },
                }
                for tool in request.tools
            ]
            body["tool_choice"] = request.tool_choice
        return body

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("LLM provider rejected authentication")
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "LLM provider rate limit was reached",
                retryable=True,
                retry_after_seconds=parse_retry_after(response.headers.get("retry-after")),
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"LLM provider returned HTTP {response.status_code}",
                retryable=True,
                retry_after_seconds=parse_retry_after(response.headers.get("retry-after")),
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"LLM provider returned HTTP {response.status_code}", retryable=False
            )

    @staticmethod
    def _mapping(response: httpx.Response) -> Mapping[object, object]:
        payload: object = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("response body is not an object")
        return payload

    def _normalize(
        self,
        *,
        request: LLMRequest,
        payload: Mapping[object, object],
        response: httpx.Response,
        response_text: str,
        latency_ms: int,
    ) -> LLMResponse:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ValueError("response has no choices")
        first = choices[0]
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise ValueError("response has no message")
        content = self._content(message.get("content"))
        tool_calls = self._tool_calls(message.get("tool_calls"))
        if content is None and not tool_calls:
            raise ValueError("response has neither content nor tool calls")
        usage = self._usage(payload.get("usage"))
        model = payload.get("model")
        response_id = payload.get("id")
        finish_reason = first.get("finish_reason")
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            provider_id=self.provider_id,
            model_id=model if isinstance(model, str) else request.model,
            request_id=(
                response.headers.get("x-request-id")
                or (response_id if isinstance(response_id, str) else None)
            ),
            usage=usage,
            latency_ms=latency_ms,
            raw_output_hash=sha256(response_text.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _content(value: object) -> str | None:
        if isinstance(value, str):
            return value if value.strip() else None
        if not isinstance(value, list):
            return None
        parts: list[str] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts) or None

    @staticmethod
    def _tool_calls(value: object) -> list[LLMToolCall]:
        if not isinstance(value, list):
            return []
        calls: list[LLMToolCall] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                continue
            function = item.get("function")
            if not isinstance(function, Mapping):
                continue
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, str):
                continue
            try:
                parsed: object = json.loads(arguments)
            except json.JSONDecodeError:
                parsed = {}
            parsed_mapping = parsed if isinstance(parsed, Mapping) else {}
            call_id = item.get("id")
            calls.append(
                LLMToolCall(
                    call_id=call_id if isinstance(call_id, str) else f"call-{index}",
                    name=name,
                    arguments={str(key): value for key, value in parsed_mapping.items()},
                )
            )
        return calls

    @classmethod
    def _usage(cls, value: object) -> LLMUsage:
        usage = value if isinstance(value, Mapping) else {}
        completion_details = usage.get("completion_tokens_details")
        completion = completion_details if isinstance(completion_details, Mapping) else {}
        prompt_details = usage.get("prompt_tokens_details")
        prompt = prompt_details if isinstance(prompt_details, Mapping) else {}
        return LLMUsage(
            input_tokens=cls._nonnegative_int(usage.get("prompt_tokens")),
            output_tokens=cls._nonnegative_int(usage.get("completion_tokens")),
            reasoning_tokens=cls._nonnegative_int(
                completion.get("reasoning_tokens") or usage.get("reasoning_tokens")
            ),
            cache_read_tokens=cls._nonnegative_int(
                prompt.get("cached_tokens") or usage.get("cache_read_tokens")
            ),
        )

    @staticmethod
    def _nonnegative_int(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    def _emit_success(self, request: LLMRequest, response: LLMResponse) -> None:
        context = current_telemetry_context()
        self._safe_emit(
            LLMCallTelemetry(
                event="llm.call.completed",
                operation=request.operation,
                provider_id=response.provider_id,
                model_id=response.model_id,
                trace_id=context.trace_id,
                run_id=context.run_id,
                request_id=response.request_id or context.request_id,
                latency_ms=response.latency_ms,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                reasoning_tokens=response.usage.reasoning_tokens,
            )
        )

    def _emit_failure(self, request: LLMRequest, started: float, error: AgentError) -> None:
        context = current_telemetry_context()
        self._safe_emit(
            LLMCallTelemetry(
                event="llm.call.failed",
                operation=request.operation,
                provider_id=self.provider_id,
                model_id=request.model,
                trace_id=context.trace_id,
                run_id=context.run_id,
                request_id=context.request_id,
                latency_ms=int((monotonic() - started) * 1000),
                error_code=error.code,
            )
        )

    def _safe_emit(self, event: LLMCallTelemetry) -> None:
        try:
            self._telemetry.emit(event)
        except Exception:  # noqa: BLE001 - observability must not change call semantics
            logger.exception(
                "llm.telemetry.failed",
                extra={
                    "operation": event.operation,
                    "provider_id": event.provider_id,
                    "model_id": event.model_id,
                },
            )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_llm_client(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    telemetry: LLMTelemetrySink | None = None,
) -> OpenAIChatLLMClient:
    if (
        settings.llm_provider != "openai_compatible"
        or settings.llm_api_key is None
        or settings.llm_base_url is None
    ):
        raise ProviderConfigurationError("a real LLM Provider must be configured")
    base_url = str(settings.llm_base_url)
    return OpenAIChatLLMClient(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=base_url,
        profile=resolve_provider_profile(
            configured_name=settings.llm_provider_name,
            base_url=base_url,
        ),
        timeout_seconds=settings.llm_timeout_seconds,
        transport=transport,
        telemetry=telemetry,
    )
