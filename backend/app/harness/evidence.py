"""Evidence visibility contracts shared by generation, validation, and persistence."""

import json
from hashlib import sha256

from app.schemas.agent_runs import EvidenceCatalogItem, EvidenceRef, EvidenceVisibility


def build_evidence_visibility(
    *,
    call_id: str,
    evidence_catalog: list[EvidenceCatalogItem],
    visible_limit: int | None = None,
) -> tuple[list[EvidenceCatalogItem], EvidenceVisibility]:
    """Freeze the exact catalog projection rendered for one Provider call."""
    visible = evidence_catalog if visible_limit is None else evidence_catalog[:visible_limit]
    truncated = [] if visible_limit is None else evidence_catalog[visible_limit:]
    canonical_catalog = json.dumps(
        [item.model_dump(mode="json") for item in evidence_catalog],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    visibility = EvidenceVisibility(
        call_id=call_id,
        catalog_hash=sha256(canonical_catalog.encode("utf-8")).hexdigest(),
        visible_refs=[EvidenceRef(kind=item.kind, id=item.id) for item in visible],
        truncated_refs=[EvidenceRef(kind=item.kind, id=item.id) for item in truncated],
    )
    return list(visible), visibility


def evidence_refs_are_visible(
    evidence_refs: list[EvidenceRef], visibility: EvidenceVisibility
) -> bool:
    allowed = {(item.kind, item.id) for item in visibility.visible_refs}
    return all((item.kind, item.id) in allowed for item in evidence_refs)
