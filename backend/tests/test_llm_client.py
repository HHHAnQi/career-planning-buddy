"""Provider-neutral LLM client compatibility and telemetry tests."""

import json

import httpx
import pytest

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderUnavailableError,
)
from app.core.telemetry import bind_telemetry_context
from app.providers.llm_client import OpenAIChatLLMClient
from app.providers.llm_contracts import (
    LLMCallTelemetry,
    LLMMessage,
    LLMProviderProfile,
    LLMRequest,
)
from app.providers.llm_profiles import resolve_provider_profile


class CapturingTelemetry:
    def __init__(self) -> None:
        self.events: list[LLMCallTelemetry] = []

    def emit(self, event: LLMCallTelemetry) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_glm_profile_maps_reasoning_and_normalizes_usage() -> None:
    captured: dict[str, object] = {}
    telemetry = CapturingTelemetry()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            headers={"x-request-id": "glm-request-1"},
            json={
                "id": "glm-completion-1",
                "model": "glm-4.7",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 13,
                    "completion_tokens_details": {"reasoning_tokens": 8},
                },
            },
        )

    profile = resolve_provider_profile(
        configured_name="auto", base_url="https://open.bigmodel.cn/api/paas/v4"
    )
    client = OpenAIChatLLMClient(
        api_key="test-only",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        profile=profile,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
        telemetry=telemetry,
    )
    with bind_telemetry_context(trace_id="trace-1", run_id="run-1"):
        response = await client.complete(
            LLMRequest(
                operation="goal_understanding",
                model="glm-4.7",
                messages=[LLMMessage(role="user", content="extract")],
                structured_output="json_object",
                reasoning="off",
                temperature=0,
                max_output_tokens=700,
            )
        )
    await client.aclose()

    assert profile.provider_id == "zhipu"
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["response_format"] == {"type": "json_object"}
    assert response.usage.input_tokens == 21
    assert response.usage.output_tokens == 13
    assert response.usage.reasoning_tokens == 8
    assert telemetry.events[0].trace_id == "trace-1"
    assert telemetry.events[0].run_id == "run-1"
    assert telemetry.events[0].request_id == "glm-request-1"


def test_generic_compatible_profile_does_not_guess_vendor_parameters() -> None:
    profile = resolve_provider_profile(
        configured_name="auto", base_url="https://gateway.example.test/v1"
    )

    assert profile == LLMProviderProfile(provider_id="openai_compatible")
    assert profile.supports_reasoning_control is False


def _sse(data: object) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}"


def _sse_stream_request(request: httpx.Request) -> httpx.Response:
    """Serve a canned OpenAI-compatible SSE stream with content, tool call
    deltas, and a final usage chunk."""
    body_lines = [
        _sse(
            {"id": "st-1", "model": "deepseek-v4", "choices": [{"delta": {"content": '{"goal"'}}]}
        ),
        _sse(
            {"id": "st-1", "model": "deepseek-v4", "choices": [{"delta": {"content": ':"agent",'}}]}
        ),
        _sse(
            {
                "id": "st-1",
                "model": "deepseek-v4",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {"name": "memory_lookup", "arguments": '{"q":"re'},
                                }
                            ]
                        }
                    }
                ],
            }
        ),
        _sse(
            {
                "id": "st-1",
                "model": "deepseek-v4",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": 'sume"}'}}]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        ),
        _sse(
            {
                "id": "st-1",
                "model": "deepseek-v4",
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 12},
            }
        ),
        "data: [DONE]",
    ]
    return httpx.Response(
        200,
        request=request,
        headers={"content-type": "text/event-stream"},
        content="\n".join(body_lines) + "\n",
    )


@pytest.mark.asyncio
async def test_complete_streamed_assembles_content_tool_calls_and_usage() -> None:
    deltas: list[str] = []
    telemetry = CapturingTelemetry()
    profile = resolve_provider_profile(
        configured_name="auto", base_url="https://api.deepseek.example/v1"
    )
    client = OpenAIChatLLMClient(
        api_key="test-only",
        base_url="https://api.deepseek.example/v1",
        profile=profile,
        timeout_seconds=5,
        transport=httpx.MockTransport(_sse_stream_request),
        telemetry=telemetry,
    )

    async def on_delta(text: str) -> None:
        deltas.append(text)

    response = await client.complete_streamed(
        LLMRequest(
            operation="planning",
            model="deepseek-v4",
            messages=[LLMMessage(role="user", content="plan")],
        ),
        on_delta=on_delta,
    )
    await client.aclose()

    assert deltas == ['{"goal"', ':"agent",']
    assert response.content == '{"goal":"agent",'
    assert response.finish_reason == "tool_calls"
    assert response.model_id == "deepseek-v4"
    assert response.request_id == "st-1"
    assert response.usage.input_tokens == 40
    assert response.usage.output_tokens == 12
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.call_id == "call-1"
    assert call.name == "memory_lookup"
    assert call.arguments == {"q": "resume"}
    assert len(response.raw_output_hash) == 64
    assert telemetry.events[-1].event == "llm.call.completed"
    assert telemetry.events[-1].output_tokens == 12


@pytest.mark.asyncio
async def test_complete_streamed_requires_stream_body_flag() -> None:
    captured: dict[str, object] = {}
    telemetry = CapturingTelemetry()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        single_chunk = _sse(
            {
                "id": "st-2",
                "model": "m",
                "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
        )
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=f"{single_chunk}\ndata: [DONE]\n",
        )

    profile = resolve_provider_profile(
        configured_name="auto", base_url="https://api.example.test/v1"
    )
    client = OpenAIChatLLMClient(
        api_key="test-only",
        base_url="https://api.example.test/v1",
        profile=profile,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
        telemetry=telemetry,
    )
    response = await client.complete_streamed(
        LLMRequest(
            operation="planning",
            model="m",
            messages=[LLMMessage(role="user", content="hi")],
        )
    )
    await client.aclose()

    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert response.content == "ok"


@pytest.mark.asyncio
async def test_complete_streamed_maps_http_error_and_empty_stream() -> None:
    profile = resolve_provider_profile(
        configured_name="auto", base_url="https://api.example.test/v1"
    )
    telemetry = CapturingTelemetry()

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    client = OpenAIChatLLMClient(
        api_key="test-only",
        base_url="https://api.example.test/v1",
        profile=profile,
        timeout_seconds=5,
        transport=httpx.MockTransport(unauthorized),
        telemetry=telemetry,
    )
    with pytest.raises(ProviderAuthenticationError):
        await client.complete_streamed(
            LLMRequest(
                operation="planning",
                model="m",
                messages=[LLMMessage(role="user", content="hi")],
            )
        )
    await client.aclose()

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content="data: [DONE]\n",
        )

    client = OpenAIChatLLMClient(
        api_key="test-only",
        base_url="https://api.example.test/v1",
        profile=profile,
        timeout_seconds=5,
        transport=httpx.MockTransport(empty),
        telemetry=telemetry,
    )
    with pytest.raises(ProviderUnavailableError):
        await client.complete_streamed(
            LLMRequest(
                operation="planning",
                model="m",
                messages=[LLMMessage(role="user", content="hi")],
            )
        )
    await client.aclose()
    failures = [e for e in telemetry.events if e.event == "llm.call.failed"]
    assert len(failures) == 2
