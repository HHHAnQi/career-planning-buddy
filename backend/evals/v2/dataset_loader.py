"""Validated V2 dataset loading and the Stage 5 compatibility adapter."""

from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.schemas.base import StrictModel
from evals.v2.contracts import (
    DatasetManifest,
    EvalCase,
    EvalProfile,
    canonical_sha256,
)

EVAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent / "datasets" / "manifest-v2.json"


class DatasetBundle(StrictModel):
    manifest: DatasetManifest
    cases: list[EvalCase]


class LegacyStage5Case(StrictModel):
    case_id: str
    category: str
    message: str
    profile: EvalProfile | None
    hint_intent: Literal["create_plan", "replan"] | None = None
    replan_mode: Literal["continue", "adjust"] | None = None
    expected_result_kind: Literal["plan", "clarification", "safe_response", "navigation"]
    expected_tools: list[Literal["memory_lookup", "rag_retrieve", "web_search"]] = Field(
        default_factory=list
    )
    live_only: bool = False
    confirmed_memories: list[dict[str, object]] = Field(default_factory=list)


def load_dataset(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> DatasetBundle:
    """Load a manifest, validate its source hash, and return strict V2 cases."""

    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    source_path = (EVAL_ROOT / manifest.source_path).resolve()
    try:
        source_path.relative_to(EVAL_ROOT)
    except ValueError as exc:
        raise ValueError("dataset source_path must remain inside backend/evals") from exc
    source_bytes = source_path.read_bytes()
    actual_hash = sha256(source_bytes).hexdigest()
    if actual_hash != manifest.source_sha256:
        raise ValueError(
            f"dataset hash mismatch: manifest={manifest.source_sha256}, actual={actual_hash}"
        )
    lines = [line for line in source_bytes.decode("utf-8").splitlines() if line.strip()]
    if len(lines) != manifest.case_count:
        raise ValueError(
            f"dataset case_count mismatch: manifest={manifest.case_count}, actual={len(lines)}"
        )
    if manifest.source_format == "legacy_stage5_jsonl":
        cases = [
            _adapt_stage5(LegacyStage5Case.model_validate_json(line), manifest)
            for line in lines
        ]
    else:
        cases = [EvalCase.model_validate_json(line) for line in lines]
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("dataset contains duplicate case_id values")
    for case in cases:
        if (case.dataset_id, case.dataset_version) != (
            manifest.dataset_id,
            manifest.dataset_version,
        ):
            raise ValueError(f"case {case.case_id} does not belong to manifest dataset")
    return DatasetBundle(manifest=manifest, cases=cases)


def filter_cases(bundle: DatasetBundle, case_ids: list[str]) -> DatasetBundle:
    """Return a derived bundle containing only ``case_ids`` in the given order.

    The manifest -- and therefore its frozen ``source_sha256`` -- is preserved;
    only the in-memory case list is narrowed. This is what PR-3's smoke runner
    uses to pick 10 Stage 5 cases by id without violating dataset integrity.
    """

    by_id = {case.case_id: case for case in bundle.cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"case_ids not found in dataset: {missing}")
    selected = [by_id[case_id] for case_id in case_ids]
    return DatasetBundle(manifest=bundle.manifest, cases=selected)


def _adapt_stage5(case: LegacyStage5Case, manifest: DatasetManifest) -> EvalCase:
    difficulty: Literal["regression", "capability", "adversarial"] = "regression"
    if case.category.startswith("tool_policy") or "repair" in case.category:
        difficulty = "capability"
    if "safety" in case.category or "risk" in case.category:
        difficulty = "adversarial"
    is_fallback_plan = (
        case.expected_result_kind == "plan" and case.category.endswith("_fallback")
    )
    allowed_statuses = (
        ["completed"]
        if case.expected_result_kind == "plan" and not is_fallback_plan
        else ["degraded"]
    )
    payload: dict[str, object] = {
        "case_id": case.case_id,
        "schema_version": "2",
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "scenario": {
            "user_request": case.message,
            "profile": case.profile.model_dump(mode="json") if case.profile else None,
            "hint_intent": case.hint_intent,
            "replan_mode": case.replan_mode,
            "initial_plan": None,
            "initial_tasks": [],
            "initial_reviews": [],
            "confirmed_memories": case.confirmed_memories,
            "unconfirmed_memory_candidates": [],
            "search_fixtures": {},
            "provider_fixtures": {},
            "planning_date": "2026-08-01",
        },
        "expected_outcome": {
            "result_kind": case.expected_result_kind,
            "allowed_run_statuses": allowed_statuses,
        },
        "trajectory_policy": {
            "expected_tools": case.expected_tools,
            "max_tool_calls": 4,
            "require_terminal_event": True,
        },
        "rubric": {
            "criteria": [
                {
                    "criterion_id": "expected_result_kind",
                    "description": "Runtime result kind must match the versioned Case expectation.",
                    "hard_gate": True,
                }
            ]
        },
        "difficulty": difficulty,
        "tags": [case.category, "stage5-adapter"],
        "fixture_version": "stage5-v1",
        "counterfactual_group_id": None,
        "variant": None,
        "fault_plan": None,
    }
    payload["fixture_hash"] = canonical_sha256(payload)
    return EvalCase.model_validate(payload)
