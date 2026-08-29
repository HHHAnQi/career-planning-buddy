"""Canonical runtime identity shared by snapshots and Eval."""

from app.runtime.versioning import RuntimeVersionIdentity, build_runtime_identity

__all__ = ["RuntimeVersionIdentity", "build_runtime_identity"]
