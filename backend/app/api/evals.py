"""HTTP control plane for Eval V2 experiments (PR-7).

Endpoints:

* POST /api/v1/eval/runs                       -> create + async submit
* GET  /api/v1/eval/runs                       -> paginated listing (PR-9b)
* GET  /api/v1/eval/runs/{experiment_id}       -> status projection
* GET  /api/v1/eval/runs/{experiment_id}/progress -> lightweight progress (PR-9b)
* GET  /api/v1/eval/runs/{experiment_id}/report -> ExperimentReport (terminal)
* POST /api/v1/eval/runs/{experiment_id}/report/regenerate -> pure rebuild (PR-9b)
* POST /api/v1/eval/runs/{experiment_id}/cancel -> staging cancel (PR-9b)

POST synchronously creates ``EvalExperiment``+``EvalTrial`` rows then spawns
the background ``EvalRunnerExecutor`` task; the request session is not held by
the background coroutine. GETs read DB rows; the report endpoint refuses to
serve an Experiment that is still ``running`` (HTTP 409 EVAL_RUN_NOT_FINISHED).
"""

from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.eval_executor import EvalRunnerExecutor
from app.api.dependencies import (
    get_eval_runner_executor,
    get_eval_service,
    require_dev,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import AppError
from app.core.security import AuthenticatedUser
from app.repositories.evals import EvalRepository
from app.runtime.versioning import build_runtime_identity
from app.schemas.errors import ErrorResponse
from app.schemas.evals import (
    EvalRunCancelResponse,
    EvalRunCreatedResponse,
    EvalRunCreateRequest,
    EvalRunListItem,
    EvalRunListResponse,
    EvalRunProgressResponse,
    EvalRunReportResponse,
    EvalRunStatusResponse,
    TrialStatusSummary,
)
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import DatasetBundle, filter_cases, load_dataset
from evals.v2.runtime_smoke import load_runtime_smoke_dataset

router = APIRouter(
    prefix="/eval/runs",
    tags=["eval-runs"],
    dependencies=[Depends(require_dev)],
)

_PROVIDER_MODE_TO_EXECUTION = {
    "mock": "mock_provider",
    "fixture": "fixture_provider",
    "live": "live_provider",
}

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _build_config(
    settings: Settings,
    *,
    manifest: DatasetManifest,
    trial_count: int,
    provider_mode: str | None,
    baseline_experiment_id: UUID | None,
    agent_variant: str | None = None,
) -> ExperimentCreate:
    identity = build_runtime_identity(settings)
    return ExperimentCreate(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.source_sha256,
        git_commit=identity.git_commit,
        graph_version=identity.graph_version,
        feature_stage=identity.feature_stage,
        prompt_version=identity.primary_prompt_version,
        model_version=settings.llm_model or "mock-v1",
        tool_version=identity.tool_contract_version,
        context_version=identity.context_version,
        memory_version=identity.memory_version,
        search_version=identity.search_version,
        eval_harness_version=identity.eval_harness_version,
        execution_mode=_PROVIDER_MODE_TO_EXECUTION[
            provider_mode or settings.eval_provider_mode
        ],
        variant_role=("candidate" if baseline_experiment_id else "baseline"),
        baseline_experiment_id=baseline_experiment_id,
        trial_count=trial_count,
        agent_variant=agent_variant,
    )


def _load_dataset(dataset_name: str) -> DatasetBundle:
    bundle = (load_runtime_smoke_dataset() if dataset_name == "runtime-smoke"
              else load_dataset())
    return bundle


def _failure_counts(payload: dict[str, object]) -> dict[str, int]:
    raw = payload.get("failure_counts")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): int(value)
        for key, value in raw.items()
        if isinstance(value, int | float)
    }


@router.get(
    "",
    response_model=EvalRunListResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_eval_runs(
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    status: Annotated[str | None, Query()] = None,
    limit: int = 50,
    offset: int = 0,
) -> EvalRunListResponse:
    """Paginated list of eval experiments (PR-9b). Newest first."""

    repo = EvalRepository(session)
    rows = await repo.list_experiments(
        status=status, limit=limit, offset=offset
    )
    items = [
        EvalRunListItem(
            experiment_id=row.id,
            status=row.status,
            execution_mode=row.execution_mode,
            dataset_id=row.dataset_id,
            trial_count=row.trial_count,
            started_at=row.started_at,
            finished_at=row.finished_at,
            cancel_requested_at=row.cancel_requested_at,
            dataset_version=row.dataset_version,
            variant_role=row.variant_role,
            baseline_experiment_id=row.baseline_experiment_id,
            agent_variant=row.agent_variant,
            git_commit=row.git_commit,
            graph_version=row.graph_version,
            feature_stage=row.feature_stage,
            prompt_version=row.prompt_version,
            model_version=row.model_version,
            tool_version=row.tool_version,
            context_version=row.context_version,
            memory_version=row.memory_version,
            search_version=row.search_version,
            eval_harness_version=row.eval_harness_version,
        )
        for row in rows
    ]
    next_offset = offset + len(items) if len(items) >= max(1, min(limit, 200)) else None
    return EvalRunListResponse(items=items, next_offset=next_offset)


@router.post(
    "",
    status_code=HTTPStatus.ACCEPTED,
    response_model=EvalRunCreatedResponse,
    responses=_ERROR_RESPONSES,
)
async def create_eval_run(
    payload: EvalRunCreateRequest,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    service: Annotated[EvalService, Depends(get_eval_service)],
    executor: Annotated[EvalRunnerExecutor, Depends(get_eval_runner_executor)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EvalRunCreatedResponse:
    bundle = _load_dataset(payload.dataset)
    if payload.cases:
        bundle = filter_cases(bundle, list(payload.cases))
    config = _build_config(
        settings,
        manifest=bundle.manifest,
        trial_count=payload.trial_count,
        provider_mode=payload.provider_mode,
        baseline_experiment_id=payload.baseline_experiment_id,
        agent_variant=payload.agent_variant,
    )
    experiment, _ = await service.create_experiment(
        dataset=bundle,
        config=config,
        run_type=payload.run_type,
        fixture_source_experiment_id=payload.fixture_source_experiment_id,
    )
    # create_experiment's session_transaction commits before we submit, so
    # the EvalExperiment + EvalTrial rows are visible to the executor task.
    executor.submit(experiment.id, bundle, grade=payload.grade)
    return EvalRunCreatedResponse(
        experiment_id=experiment.id,
        status="draft",
        status_url=f"/api/v1/eval/runs/{experiment.id}",
        report_url=f"/api/v1/eval/runs/{experiment.id}/report",
    )


@router.get(
    "/{experiment_id}",
    response_model=EvalRunStatusResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
               404: {"model": ErrorResponse}},
)
async def get_eval_run_status(
    experiment_id: UUID,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> EvalRunStatusResponse:
    repo = EvalRepository(session)
    experiment = await repo.get_experiment(experiment_id)
    if experiment is None:
        raise AppError(
            code="EVAL_EXPERIMENT_NOT_FOUND",
            message="Eval Experiment was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    trials = await repo.list_trials(experiment_id)
    trial_summaries = [
        TrialStatusSummary(
            trial_id=trial.id,
            case_id=trial.case_id,
            status=trial.status,
            run_status=None,
            result_kind=None,
            error_code=trial.error_code,
            fixture_source_trial_id=trial.fixture_source_trial_id,
        )
        for trial in trials
    ]
    return EvalRunStatusResponse(
        experiment_id=experiment.id,
        status=str(experiment.status),
        dataset_id=experiment.dataset_id,
        trial_count=len(trials),
        started_at=experiment.started_at,
        finished_at=experiment.finished_at,
        cancel_requested_at=experiment.cancel_requested_at,
        trials=trial_summaries,
        execution_mode=experiment.execution_mode,
        dataset_version=experiment.dataset_version,
        variant_role=experiment.variant_role,
        baseline_experiment_id=experiment.baseline_experiment_id,
        agent_variant=experiment.agent_variant,
        git_commit=experiment.git_commit,
        graph_version=experiment.graph_version,
        feature_stage=experiment.feature_stage,
        prompt_version=experiment.prompt_version,
        model_version=experiment.model_version,
        tool_version=experiment.tool_version,
        context_version=experiment.context_version,
        memory_version=experiment.memory_version,
        search_version=experiment.search_version,
        eval_harness_version=experiment.eval_harness_version,
    )


@router.get(
    "/{experiment_id}/progress",
    response_model=EvalRunProgressResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
               404: {"model": ErrorResponse}},
)
async def get_eval_run_progress(
    experiment_id: UUID,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> EvalRunProgressResponse:
    """Lightweight progress view (PR-9b).

    Returns Trial-status counts rather than the full graded report. The
    ``in_flight_trial_ids`` list surfaces only Trials currently marked
    ``running`` -- we do NOT infer "current step" inside a Trial; if a
    fine-grained view is later needed we can surface ``agent_steps.event_type``
    of the latest non-terminal row, but for v1 we keep the contract narrow.
    """

    repo = EvalRepository(session)
    experiment = await repo.get_experiment(experiment_id)
    if experiment is None:
        raise AppError(
            code="EVAL_EXPERIMENT_NOT_FOUND",
            message="Eval Experiment was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    trials = await repo.list_trials(experiment_id)
    counts = {"completed": 0, "running": 0, "pending": 0,
              "failed": 0, "cancelled": 0, "timed_out": 0}
    in_flight: list[UUID] = []
    for trial in trials:
        counts[trial.status] = counts.get(trial.status, 0) + 1
        if trial.status == "running":
            in_flight.append(trial.id)
    total = max(len(trials), 1)
    terminal = sum(
        counts.get(s, 0)
        for s in ("completed", "failed", "cancelled", "timed_out")
    )
    return EvalRunProgressResponse(
        experiment_id=experiment.id,
        status=experiment.status,
        trial_count=len(trials),
        completed_count=counts["completed"],
        running_count=counts["running"],
        pending_count=counts["pending"],
        failed_count=counts["failed"],
        cancelled_count=counts["cancelled"],
        timed_out_count=counts["timed_out"],
        in_flight_trial_ids=in_flight,
        cancel_requested_at=experiment.cancel_requested_at,
        estimated_progress=round(terminal / total, 6),
    )


@router.get(
    "/{experiment_id}/report",
    response_model=EvalRunReportResponse,
    responses=_ERROR_RESPONSES,
)
async def get_eval_run_report(
    experiment_id: UUID,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    service: Annotated[EvalService, Depends(get_eval_service)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> EvalRunReportResponse:
    repo = EvalRepository(session)
    experiment = await repo.get_experiment(experiment_id)
    if experiment is None:
        raise AppError(
            code="EVAL_EXPERIMENT_NOT_FOUND",
            message="Eval Experiment was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    smoke = load_runtime_smoke_dataset()
    if experiment.dataset_id == smoke.manifest.dataset_id:
        dataset = smoke
    else:
        dataset = load_dataset()
    if experiment.status == "running":
        raise AppError(
            code="EVAL_RUN_NOT_FINISHED",
            message="Experiment is still running; check status_url for completion.",
            status_code=HTTPStatus.CONFLICT,
        )
    report = await service.build_report(experiment_id, dataset)
    payload = report.to_dict()
    trial_count_val: int = int(payload["trial_count"])  # type: ignore[call-overload]
    completed_val: int = int(payload["completed_trial_count"])  # type: ignore[call-overload]
    scored_val: int = int(payload["scored_trial_count"])  # type: ignore[call-overload]
    hard_gate_val: float = float(payload["hard_gate_pass_fraction"])  # type: ignore[arg-type]
    trials_value = payload["trials"]
    trials_list: list[dict[str, object]] = (
        trials_value if isinstance(trials_value, list) else []
    )
    # PR-9a: pass-through case_stats + experiment_stats derived in
    # build_report. Both default empty for back-compat with pre-PR-9a
    # consumers of the report response schema.
    case_stats_value = payload.get("case_stats") or {}
    case_stats_dict: dict[str, dict[str, object]] = {
        str(key): value
        for key, value in case_stats_value.items()
        if isinstance(value, dict)
    } if isinstance(case_stats_value, dict) else {}
    experiment_stats_value = payload.get("experiment_stats")
    experiment_stats_dict: dict[str, object] | None = (
        experiment_stats_value
        if isinstance(experiment_stats_value, dict)
        else None
    )
    return EvalRunReportResponse(
        experiment_id=UUID(str(payload["experiment_id"])),
        experiment_status=str(payload["experiment_status"]),
        trial_count=trial_count_val,
        completed_trial_count=completed_val,
        scored_trial_count=scored_val,
        hard_gate_pass_fraction=hard_gate_val,
        any_score_generated=bool(payload["any_score_generated"]),
        trials=trials_list,
        case_stats=case_stats_dict,
        experiment_stats=experiment_stats_dict,
        failure_counts=_failure_counts(payload),
        revision=int(experiment.report_revision),
        cancel_requested_at=experiment.cancel_requested_at,
    )


@router.post(
    "/{experiment_id}/report/regenerate",
    response_model=EvalRunReportResponse,
    responses=_ERROR_RESPONSES,
)
async def regenerate_eval_run_report(
    experiment_id: UUID,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    service: Annotated[EvalService, Depends(get_eval_service)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> EvalRunReportResponse:
    """Pure-aggregate rebuild of the report (PR-9b).

    Never spawns Trials, calls Providers, or touches their audit rows.
    Bumps ``report_revision`` only when the content hash of the
    rebuilt report changed.
    """

    repo = EvalRepository(session)
    experiment = await repo.get_experiment(experiment_id)
    if experiment is None:
        raise AppError(
            code="EVAL_EXPERIMENT_NOT_FOUND",
            message="Eval Experiment was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    smoke = load_runtime_smoke_dataset()
    dataset = (
        smoke if experiment.dataset_id == smoke.manifest.dataset_id
        else load_dataset()
    )
    report = await service.regenerate_report(experiment_id, dataset)
    payload = report.to_dict()
    # Refresh the row to read the potentially-bumped revision/hash + the
    # cancel timestamp in one go.
    await session.refresh(experiment)
    trial_count_val: int = int(payload["trial_count"])  # type: ignore[call-overload]
    completed_val: int = int(payload["completed_trial_count"])  # type: ignore[call-overload]
    scored_val: int = int(payload["scored_trial_count"])  # type: ignore[call-overload]
    hard_gate_val: float = float(payload["hard_gate_pass_fraction"])  # type: ignore[arg-type]
    trials_value = payload["trials"]
    trials_list: list[dict[str, object]] = (
        trials_value if isinstance(trials_value, list) else []
    )
    case_stats_value = payload.get("case_stats") or {}
    case_stats_dict: dict[str, dict[str, object]] = {
        str(key): value
        for key, value in case_stats_value.items()
        if isinstance(value, dict)
    } if isinstance(case_stats_value, dict) else {}
    experiment_stats_value = payload.get("experiment_stats")
    experiment_stats_dict: dict[str, object] | None = (
        experiment_stats_value
        if isinstance(experiment_stats_value, dict)
        else None
    )
    return EvalRunReportResponse(
        experiment_id=UUID(str(payload["experiment_id"])),
        experiment_status=str(payload["experiment_status"]),
        trial_count=trial_count_val,
        completed_trial_count=completed_val,
        scored_trial_count=scored_val,
        hard_gate_pass_fraction=hard_gate_val,
        any_score_generated=bool(payload["any_score_generated"]),
        trials=trials_list,
        case_stats=case_stats_dict,
        experiment_stats=experiment_stats_dict,
        failure_counts=_failure_counts(payload),
        revision=int(experiment.report_revision),
        cancel_requested_at=experiment.cancel_requested_at,
    )


@router.post(
    "/{experiment_id}/cancel",
    status_code=HTTPStatus.ACCEPTED,
    response_model=EvalRunCancelResponse,
    responses=_ERROR_RESPONSES,
)
async def request_eval_run_cancel(
    experiment_id: UUID,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    service: Annotated[EvalService, Depends(get_eval_service)],
    executor: Annotated[EvalRunnerExecutor, Depends(get_eval_runner_executor)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> EvalRunCancelResponse:
    """Stage a Cancel request against a running or queued Experiment.

    Idempotent: returns 202 even when the Experiment is already terminal,
    in which case ``cancel_requested=false`` and no timestamp is stamped.
    ``status`` in the response is the Experiment status *at the moment*
    the request was processed; subsequent status transitions to
    ``cancelled`` happen in the background once in-flight Trials land
    in their terminal states.
    """

    repo = EvalRepository(session)
    experiment = await repo.get_experiment(experiment_id)
    if experiment is None:
        raise AppError(
            code="EVAL_EXPERIMENT_NOT_FOUND",
            message="Eval Experiment was not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    pre_status = experiment.status
    requested, cancel_at = await service.set_cancel_requested(experiment_id)
    if requested:
        await executor.request_cancel(experiment_id)
    return EvalRunCancelResponse(
        experiment_id=experiment_id,
        status=pre_status,
        cancel_requested=requested,
        cancel_requested_at=cancel_at,
    )
