"""ProviderCall audit + frozen Fixture bundle (PR-5).

Re-exports the public surface:

* ``ProviderCallRecorder`` -- per-Run sequence owner + row writer.
* ``ProviderCallRepository`` -- CRUD for the three tables.
* ``ProviderInvocationResult`` -- materialised view of one call.
* The ``Audit{Planning,Search,Embedding}Provider`` and
  ``Fixture{Planning,Search,Embedding}Provider`` wrappers.

PR-5 callers (``trial_runner.py::_build_executor``) construct one
``ProviderCallRecorder`` (and optionally one ``FixtureStore``) per Run and
deliver them to the three provider wrappers.
"""

from app.harness.provider_calls.audit import (
    AuditEmbeddingProvider,
    AuditPlanningProvider,
    AuditSearchProvider,
)
from app.harness.provider_calls.fixture_store import (
    MAX_FIXTURE_PAYLOAD_BYTES,
    FixtureDesyncError,
    FixtureEntry,
    FixturePayloadPolicyError,
    FixtureStore,
)
from app.harness.provider_calls.providers import (
    FixtureEmbeddingProvider,
    FixturePlanningProvider,
    FixtureSearchProvider,
)
from app.harness.provider_calls.recorder import (
    ProviderCallRecorder,
    ProviderInvocationResult,
)
from app.harness.provider_calls.repository import ProviderCallRepository
from app.harness.provider_calls.retry import (
    LiveEvalCallController,
    LiveEvalRetryPolicy,
    RetryAttemptState,
    RetryingEmbeddingProvider,
    RetryingPlanningProvider,
    RetryingSearchProvider,
)

__all__ = [
    "AuditEmbeddingProvider",
    "AuditPlanningProvider",
    "AuditSearchProvider",
    "FixtureDesyncError",
    "FixtureEmbeddingProvider",
    "FixtureEntry",
    "FixturePayloadPolicyError",
    "FixturePlanningProvider",
    "FixtureSearchProvider",
    "FixtureStore",
    "LiveEvalCallController",
    "LiveEvalRetryPolicy",
    "MAX_FIXTURE_PAYLOAD_BYTES",
    "ProviderCallRecorder",
    "ProviderCallRepository",
    "ProviderInvocationResult",
    "RetryAttemptState",
    "RetryingEmbeddingProvider",
    "RetryingPlanningProvider",
    "RetryingSearchProvider",
]
