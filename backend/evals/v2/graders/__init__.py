"""V2 six-domain deterministic Graders package.

Re-exports the base types (for ``test_evidence_authorization``) and the
registry entry points (for ``EvalService.grade_trial``).
"""

from evals.v2.graders.base import (
    AuthorizedView,
    EvidenceAccessDenied,
    EvidenceItem,
    EvidenceKind,
    Grader,
    as_dict_list,
    as_int,
    as_list,
    authorize,
)
from evals.v2.graders.registry import (
    allowed_kinds_snapshot,
    grade_all,
    registered_graders,
)

__all__ = [
    "AuthorizedView",
    "EvidenceAccessDenied",
    "EvidenceItem",
    "EvidenceKind",
    "Grader",
    "allowed_kinds_snapshot",
    "as_dict_list",
    "as_int",
    "as_list",
    "authorize",
    "grade_all",
    "registered_graders",
]
