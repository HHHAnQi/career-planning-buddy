"""Search Provider protocol plus Mock and Baidu implementations."""

import asyncio
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StructuredOutputError,
    parse_retry_after,
)
from app.core.config import Settings
from app.schemas.base import StrictModel

SourceType = Literal["official", "job_board", "blog", "community", "other"]
_CONTROL_OR_TAG = re.compile(r"[\x00-\x1f\x7f]|<[^>]{0,200}>")
_TOKEN = re.compile(r"[\u3400-\u9fff]+|[A-Za-z0-9][A-Za-z0-9+.#_-]*")
_PRIORITY = re.compile(
    r"岗位|招聘|职位|要求|技能|技术|校招|社招|面试|薪资|趋势|目标|202[0-9]|"
    r"job|role|skill|tech|career|interview|salary|hiring",
    re.IGNORECASE,
)


class SearchResultItem(StrictModel):
    url: str
    title: str | None
    snippet: str
    source_type: SourceType
    reliability: float = Field(ge=0, le=1)
    retrieved_at: datetime
    provider_request_id: str | None = None
    published_at: datetime | None = None


class SearchProvider(Protocol):
    provider_name: str

    async def search(
        self, *, query: str, limit: int, freshness_days: int | None
    ) -> list[SearchResultItem]: ...


def compact_baidu_search_query(value: str, *, max_weight: int = 72) -> str:
    """Return a bounded deterministic query without forwarding prompt/context blobs."""
    cleaned = _CONTROL_OR_TAG.sub(" ", value)
    tokens = _TOKEN.findall(cleaned)
    prioritized = [token for token in tokens if _PRIORITY.search(token)]
    ordered = list(dict.fromkeys([*prioritized, *tokens]))
    selected: list[str] = []
    weight = 0
    for token in ordered:
        token_weight = sum(2 if "\u3400" <= char <= "\u9fff" else 1 for char in token)
        if token_weight == 0 or weight + token_weight > max_weight:
            continue
        selected.append(token)
        weight += token_weight
    result = " ".join(selected).strip()
    if not result:
        raise ValueError("search query has no usable terms")
    return result


def _normalized_hostname(url: str) -> str:
    try:
        raw = urlsplit(url).hostname
        if not raw:
            return ""
        return raw.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return ""


def host_matches(host: str, domain: str) -> bool:
    normalized_domain = domain.rstrip(".").lower()
    return bool(
        host
        and normalized_domain
        and (host == normalized_domain or host.endswith(f".{normalized_domain}"))
    )


def classify_source(url: str) -> tuple[SourceType, float]:
    host = _normalized_hostname(url)
    official = ("gov.cn", "edu.cn", "org.cn", "open.baidu.com", "cloud.baidu.com")
    job = ("zhipin.com", "liepin.com", "51job.com", "zhaopin.com")
    community = ("zhihu.com", "reddit.com", "stackoverflow.com", "v2ex.com")
    blog = ("medium.com", "csdn.net", "cnblogs.com", "juejin.cn")
    if any(host_matches(host, domain) for domain in official):
        return "official", 0.9
    if any(host_matches(host, domain) for domain in job):
        return "job_board", 0.75
    if any(host_matches(host, domain) for domain in community):
        return "community", 0.45
    if any(host_matches(host, domain) for domain in blog):
        return "blog", 0.6
    return "other", 0.5


class BaiduSearchProvider:
    provider_name = "baidu"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        edition: Literal["lite", "standard"],
        max_results: int,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._edition = edition
        self._max_results = max_results
        self._timeout = timeout_seconds
        self._transport = transport
        self._client = (
            httpx.AsyncClient(timeout=self._timeout) if transport is None else None
        )
        self.last_trace: dict[str, object] = {}

    async def search(
        self, *, query: str, limit: int, freshness_days: int | None
    ) -> list[SearchResultItem]:
        compact = compact_baidu_search_query(query)
        top_k = min(limit, self._max_results)
        payload: dict[str, object] = {
            "messages": [{"role": "user", "content": compact}],
            "edition": self._edition,
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": top_k}],
        }
        if freshness_days is not None:
            payload["freshness_days"] = freshness_days
        started = monotonic()
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._base_url,
                    headers={
                        "X-Appbuilder-Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    response = await client.post(
                        self._base_url,
                        headers={
                            "X-Appbuilder-Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Baidu search timed out", retryable=True
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderUnavailableError(
                "Baidu search network failure", retryable=True
            ) from exc
        self.last_trace = {
            "query_hash": sha256(compact.encode()).hexdigest(),
            "query_length": len(compact),
            "latency_ms": int((monotonic() - started) * 1000),
        }
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Baidu search authentication failed")
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Baidu search rate limited",
                retryable=True,
                retry_after_seconds=parse_retry_after(
                    response.headers.get("retry-after")
                ),
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                "Baidu search service unavailable",
                retryable=True,
                retry_after_seconds=parse_retry_after(
                    response.headers.get("retry-after")
                ),
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                "Baidu search request rejected", retryable=False
            )
        try:
            body = response.json()
            if not isinstance(body, Mapping):
                raise TypeError
            references = body.get("references")
            if references is None and isinstance(body.get("result"), Mapping):
                result = body["result"]
                references = result.get("references")
            if not isinstance(references, list):
                raise TypeError
            request_id = self._string(body.get("request_id") or body.get("id"))
            self.last_trace["request_id"] = request_id or response.headers.get("x-request-id", "")
            return [
                item
                for raw in references[:top_k]
                if (item := self._map_reference(raw, request_id)) is not None
            ]
        except (TypeError, ValueError, ValidationError) as exc:
            raise StructuredOutputError("Baidu search returned an invalid response") from exc

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    @classmethod
    def _map_reference(cls, raw: object, request_id: str | None) -> SearchResultItem | None:
        if not isinstance(raw, Mapping):
            return None
        url = cls._string(raw.get("url") or raw.get("link"))
        snippet = cls._string(raw.get("snippet") or raw.get("content") or raw.get("abstract"))
        if not url or not snippet:
            return None
        source_type, reliability = classify_source(url)
        published = raw.get("published_at") or raw.get("date")
        published_at: datetime | None = None
        if isinstance(published, str):
            try:
                published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                published_at = None
        return SearchResultItem(
            url=url,
            title=cls._string(raw.get("title")),
            snippet=snippet,
            source_type=source_type,
            reliability=reliability,
            retrieved_at=datetime.now(UTC),
            provider_request_id=request_id,
            published_at=published_at,
        )

    @staticmethod
    def _string(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None


class MockSearchProvider:
    """Offline fixture Provider; results are explicit test data, never live search."""

    provider_name = "mock"

    async def search(
        self, *, query: str, limit: int, freshness_days: int | None
    ) -> list[SearchResultItem]:
        del freshness_days
        if "[mock:search-timeout]" in query:
            await asyncio.sleep(60)
        if "[mock:search-error]" in query:
            raise ProviderUnavailableError("Mock Search Provider unavailable")
        now = datetime.now(UTC)
        fixtures = [
            SearchResultItem(
                url="https://example.test/career/agent-engineer",
                title="Agent Engineer Role Guide (Mock Fixture)",
                snippet="固定测试数据：岗位通常要求 Python、评测、可观测性与可靠性工程能力。",
                source_type="official",
                reliability=0.9,
                retrieved_at=now,
            ),
            SearchResultItem(
                url="https://example.test/jobs/agent-engineer?utm_source=fixture",
                title="Agent Engineer Job Signals (Mock Fixture)",
                snippet="固定测试数据：可展示项目、测试证据和系统设计说明有助于验证能力。",
                source_type="job_board",
                reliability=0.75,
                retrieved_at=now,
            ),
            SearchResultItem(
                url="https://example.test/career/agent-engineer#duplicate",
                title="Duplicate URL Fixture",
                snippet="该条用于验证 URL 规范化去重。",
                source_type="other",
                reliability=0.5,
                retrieved_at=now,
            ),
        ]
        return fixtures[:limit]


def build_search_provider(settings: Settings) -> SearchProvider:
    """Build only the configured provider; a real-provider failure never falls back."""
    if settings.search_provider == "mock":
        return MockSearchProvider()
    if settings.baidu_search_api_key is None:
        raise ProviderConfigurationError("baidu search requires BAIDU_SEARCH_API_KEY")
    return BaiduSearchProvider(
        api_key=settings.baidu_search_api_key.get_secret_value(),
        base_url=str(settings.baidu_search_base_url),
        edition=settings.baidu_search_edition,
        max_results=settings.baidu_search_max_results,
        timeout_seconds=settings.baidu_search_timeout_seconds,
    )
