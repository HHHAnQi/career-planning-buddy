"""PR-9c.2 Stage B pre-flight — relax provider_calls token CHECK for error LLM.

Background
----------
The original ``ck_provider_calls_tokens_pair`` CHECK constraint (from
migration ``20260807_0010``) encoded a single invariant::

    (provider_kind IN ('embedding','search'))
        = (tokens_in IS NULL AND tokens_out IS NULL)

The intent was "LLM rows must carry token counts; embedding/search rows
must not." But the invariant forgot the **error** branch: when an LLM
provider call fails with ``status='error'`` (e.g.
``PROVIDER_RATE_LIMITED`` / ``AGENT_EXECUTION_FAILED``), no usage info is
returned, so ``tokens_in`` / ``tokens_out`` are legitimately NULL. The
recorder (``app/harness/provider_calls/recorder.py``) writes exactly
that shape, and Stage B's real-graph retry path (which exercises
``MockPlanningProvider``'s ``PROVIDER_RATE_LIMITED`` injection) trips the
CHECK, which surfaces as an ``IntegrityError`` that the
``AgentRunExecutor.execute`` ``except Exception:`` block then swallows
into ``AGENT_EXECUTION_FAILED`` -- killing the Trial with no useful
diagnostic.

Stage A never tripped this because PairSmoke fixture mapping bypasses
``generate_agent_turn`` entirely. Stage B uses the real graph + provider
recorder, so the constraint design flaw becomes fatal.

Fix
---
Split the overloaded single CHECK into two single-responsibility CHECKs
so the kind-vs-tokens relationship and the status-vs-tokens relationship
can be reasoned about independently:

* ``ck_provider_calls_tokens_kind_pair`` -- preserves the original
  embedding/search ⇒ NULL tokens contract verbatim (no behaviour change
  for non-LLM kinds).

* ``ck_provider_calls_llm_success_tokens`` -- NEW rule: an ``llm`` row
  is only required to carry non-NULL tokens when it succeeded. Error
  rows (``status='error'``) are exempt because no usage info is
  available; cancelled rows (``status='cancelled'``) are exempt for the
  same reason (recorder already writes NULL there too, and
  ``status='cancelled'`` was previously caught only because kind=llm
  forced non-NULL tokens -- same latent bug as error rows).

The success-LLM contract is unchanged: a successful LLM call without
tokens still violates the constraint. The migration's DOWN rebuilds the
original single constraint so the change is reversible.

PR-9c.2 Commit 3.6 — Stage B fault-injection audit-schema fix.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260815_0018"
down_revision: str | None = "20260807_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop the overloaded single CHECK.
    op.drop_constraint(
        "ck_provider_calls_tokens_pair",
        "provider_calls",
        type_="check",
    )

    # 2. Re-establish the kind-vs-tokens invariant, expressed as a
    #    disjunction (not an equality) so it composes cleanly with (3)
    #    below. Semantics: a non-LLM row must have NULL tokens; an LLM
    #    row is unconstrained at this layer (the success-token
    #    requirement lives in (3)).
    op.create_check_constraint(
        "ck_provider_calls_tokens_kind_pair",
        "provider_calls",
        "(provider_kind IN ('embedding','search') "
        "  AND tokens_in IS NULL AND tokens_out IS NULL) "
        "OR (provider_kind = 'llm')",
    )

    # 3. New invariant: a successful, non-cancelled LLM row must carry
    #    real token counts. Error / cancelled LLM rows are exempt because
    #    usage info is structurally unavailable on the failure path.
    op.create_check_constraint(
        "ck_provider_calls_llm_success_tokens",
        "provider_calls",
        "(provider_kind <> 'llm') "
        "OR (status IN ('error', 'cancelled')) "
        "OR (tokens_in IS NOT NULL AND tokens_out IS NOT NULL)",
    )


def downgrade() -> None:
    # Restore the original overloaded single CHECK (keeps the migration
    # reversible for environments pinned to pre-Stage-B semantics; the
    # latent error-path bug is accepted on downgrade).
    op.drop_constraint(
        "ck_provider_calls_llm_success_tokens",
        "provider_calls",
        type_="check",
    )
    op.drop_constraint(
        "ck_provider_calls_tokens_kind_pair",
        "provider_calls",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_calls_tokens_pair",
        "provider_calls",
        "(provider_kind IN ('embedding','search')) "
        "= (tokens_in IS NULL AND tokens_out IS NULL)",
    )
