"""Top-level HTTP router composition."""

from fastapi import APIRouter

from app.api.agent_runs import router as agent_runs_router
from app.api.auth import router as auth_router
from app.api.dev import router as dev_router
from app.api.evals import router as eval_router
from app.api.goal_briefs import router as goal_briefs_router
from app.api.health import router as health_router
from app.api.interviews import router as interviews_router
from app.api.memories import router as memories_router
from app.api.metrics import router as metrics_router
from app.api.pairwise_calibration import router as pairwise_router
from app.api.plans import plans_router, task_adjustments_router, tasks_router
from app.api.profile import router as profile_router
from app.api.resume_assessments import router as resume_assessments_router
from app.api.resumes import job_target_router, resume_router
from app.api.reviews import router as reviews_router

api_router = APIRouter()
api_router.include_router(metrics_router)
api_router.include_router(health_router)

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(profile_router)
api_v1_router.include_router(resume_router)
api_v1_router.include_router(job_target_router)
api_v1_router.include_router(resume_assessments_router)
api_v1_router.include_router(interviews_router)
api_v1_router.include_router(goal_briefs_router)
api_v1_router.include_router(agent_runs_router)
api_v1_router.include_router(plans_router)
api_v1_router.include_router(tasks_router)
api_v1_router.include_router(task_adjustments_router)
api_v1_router.include_router(reviews_router)
api_v1_router.include_router(memories_router)
api_v1_router.include_router(eval_router)
api_v1_router.include_router(pairwise_router)
api_v1_router.include_router(dev_router)
api_router.include_router(api_v1_router)
