"""Developer-only Trace, Replay, and Eval HTTP endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_dev_trace_service, require_dev
from app.core.security import AuthenticatedUser
from app.schemas.dev import (
    DevRunDetail,
    DevRunListResponse,
    EvalDatasetListResponse,
    EvalDatasetSummary,
    EvalExperimentResponse,
    EvalStartRequest,
    EvalStartResponse,
    RepairReportResponse,
    ReplayDiff,
    ReplayRequest,
    ReplayResponse,
    UsageReportResponse,
)
from app.services.dev import DevTraceService
from evals.resume_runner import load_resume_experiment, run_resume_evaluation
from evals.runner import load_cases, load_experiment, run_evaluation

router = APIRouter(prefix="/dev", tags=["developer"], dependencies=[Depends(require_dev)])


@router.get("/runs", response_model=DevRunListResponse)
async def list_runs(
    service: Annotated[DevTraceService, Depends(get_dev_trace_service)],
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    run_status: Annotated[str | None, Query(alias="status")] = None,
    result_kind: str | None = None,
    error_code: str | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DevRunListResponse:
    return await service.list_runs(
        status=run_status,
        result_kind=result_kind,
        error_code=error_code,
        cursor=cursor,
        limit=limit,
    )


@router.get("/usage-report", response_model=UsageReportResponse)
async def get_usage_report(
    service: Annotated[DevTraceService, Depends(get_dev_trace_service)],
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> UsageReportResponse:
    """Cost, latency, and provider-health aggregates over the window."""

    return await service.usage_report(days=days)


@router.get("/repair-report", response_model=RepairReportResponse)
async def get_repair_report(
    service: Annotated[DevTraceService, Depends(get_dev_trace_service)],
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> RepairReportResponse:
    """Repair-mechanism outcomes and fallback-reason distribution."""

    return await service.repair_report(days=days)


@router.get("/runs/{run_id}", response_model=DevRunDetail)
async def get_run(
    run_id: UUID,
    service: Annotated[DevTraceService, Depends(get_dev_trace_service)],
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
) -> DevRunDetail:
    return await service.get_run(run_id)


@router.post(
    "/runs/{run_id}/replay",
    response_model=ReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_run(
    run_id: UUID,
    payload: ReplayRequest,
    service: Annotated[DevTraceService, Depends(get_dev_trace_service)],
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
) -> ReplayResponse:
    if payload.tool_mode is not None:
        return await service.legacy_trace_clone(run_id, tool_mode=payload.tool_mode)
    return await service.replay_v2(
        run_id,
        mode=payload.mode,
        target_runtime_bundle_id=payload.target_runtime_bundle_id,
    )


@router.get("/runs/{run_id}/replay-diff", response_model=ReplayDiff)
async def get_replay_diff(
    run_id: UUID,
    service: Annotated[DevTraceService, Depends(get_dev_trace_service)],
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
) -> ReplayDiff:
    return await service.replay_diff(run_id)


@router.get("/evals/datasets", response_model=EvalDatasetListResponse)
async def list_eval_datasets(
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
) -> EvalDatasetListResponse:
    return EvalDatasetListResponse(
        items=[
            EvalDatasetSummary(
                dataset_id="stage5-v1",
                case_count=len(load_cases()),
                description="30 deterministic Stage 5 planning, repair, replan, and safety cases",
            ),
            EvalDatasetSummary(
                dataset_id="resume-agent-v1",
                case_count=10,
                description=(
                    "Diagnostic-only resume context, faithfulness, prompt-injection, "
                    "sparse-evidence, and determinism cases; not an Eval V2 release gate"
                ),
            ),
        ]
    )


@router.post(
    "/evals/experiments",
    response_model=EvalStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_eval(
    payload: EvalStartRequest,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
) -> EvalStartResponse:
    if payload.dataset_id not in {"stage5-v1", "resume-agent-v1"}:
        from app.core.exceptions import AppError

        raise AppError(
            code="NOT_FOUND_EVAL_DATASET",
            message="evaluation dataset was not found",
            status_code=404,
        )
    report = (
        run_resume_evaluation()
        if payload.dataset_id == "resume-agent-v1"
        else await run_evaluation(case_limit=payload.case_limit, persist=True)
    )
    return EvalStartResponse(experiment_id=str(report["experiment_id"]), status="completed")


@router.get("/evals/experiments/{experiment_id}", response_model=EvalExperimentResponse)
async def get_eval(
    experiment_id: str,
    _dev: Annotated[AuthenticatedUser, Depends(require_dev)],
) -> EvalExperimentResponse:
    report = load_experiment(experiment_id) or load_resume_experiment(experiment_id)
    return EvalExperimentResponse(
        experiment_id=experiment_id,
        status="completed" if report is not None else "not_found",
        report=report,
    )
