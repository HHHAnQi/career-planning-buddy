from __future__ import annotations

from pathlib import Path

import pytest

from app.api.evals import _build_config as build_api_config
from app.core.config import Settings
from app.harness.snapshots import SnapshotService
from app.runtime import versioning
from evals.v2.__main__ import _build_config as build_cli_config
from evals.v2.runtime_smoke import load_runtime_smoke_dataset


def test_explicit_ci_git_commit_wins() -> None:
    settings = Settings(
        _env_file=None,
        app_git_commit="A" * 40,
    )
    identity = versioning.build_runtime_identity(settings)
    assert identity.git_commit == "a" * 40


def test_git_commit_is_unknown_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise FileNotFoundError

    monkeypatch.setattr("app.runtime.versioning.subprocess.run", unavailable)
    assert versioning.resolve_git_commit(None, project_root=Path(".")) == "unknown"


def test_snapshot_api_and_cli_share_canonical_identity() -> None:
    settings = Settings(
        _env_file=None,
        app_git_commit="1" * 40,
        search_provider="mock",
        eval_provider_mode="mock",
    )
    identity = versioning.build_runtime_identity(settings)
    snapshot = SnapshotService.build_config(settings)
    bundle = load_runtime_smoke_dataset()
    api_config = build_api_config(
        settings,
        manifest=bundle.manifest,
        trial_count=1,
        provider_mode="mock",
        baseline_experiment_id=None,
    )
    cli_config = build_cli_config(settings, bundle, trial_count=1)

    assert snapshot.graph_version == identity.graph_version == "stage6b-v1"
    assert snapshot.feature_stage == identity.feature_stage == 6
    assert snapshot.prompt_versions == identity.prompt_versions
    assert api_config.prompt_version == snapshot.prompt_versions["career_planning"]
    assert api_config.model_dump(exclude={"experiment_id"}) == cli_config.model_dump(
        exclude={"experiment_id"}
    )
    assert api_config.search_version == "mock-search-v1"
    assert api_config.eval_harness_version == "eval-harness-v2"


def test_historical_stage5_snapshot_still_validates() -> None:
    current = SnapshotService.build_config(Settings(_env_file=None))
    historical = current.model_copy(
        update={"graph_version": "stage5-v1", "feature_stage": 5}
    )
    assert historical.feature_stage == 5
