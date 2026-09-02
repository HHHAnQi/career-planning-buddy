"""Safe real Baidu Search Provider verification; never prints credentials or queries."""

import asyncio
import json

from app.core.config import get_settings
from app.providers.search import BaiduSearchProvider, build_search_provider


async def main() -> None:
    provider = build_search_provider(get_settings())
    if not isinstance(provider, BaiduSearchProvider):
        raise RuntimeError("SEARCH_PROVIDER must be baidu for real validation")
    outputs: list[dict[str, object]] = []
    for query, freshness_days in (
        ("2026 AI 后端岗位技能要求", None),
        ("今天 AI Agent 招聘市场最新变化", 7),
    ):
        rows = await provider.search(
            query=query,
            limit=5,
            freshness_days=freshness_days,
        )
        outputs.append(
            {
                "request_id": provider.last_trace.get("request_id"),
                "result_count": len(rows),
                "query_hash": provider.last_trace.get("query_hash"),
                "query_length": provider.last_trace.get("query_length"),
                "latency_ms": provider.last_trace.get("latency_ms"),
                "source_type_counts": {
                    kind: sum(row.source_type == kind for row in rows)
                    for kind in ("official", "job_board", "blog", "community", "other")
                },
            }
        )
    print(json.dumps(outputs, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
